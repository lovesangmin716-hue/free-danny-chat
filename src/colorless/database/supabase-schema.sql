create table if not exists public.app_state (
  id text primary key,
  state jsonb not null,
  updated_at timestamptz not null default timezone('utc', now())
);

alter table public.app_state drop constraint if exists app_state_id_check;

alter table public.app_state enable row level security;

-- Normalized application storage. app_state remains temporarily as a rollback
-- source while the row-level migration is verified.
create table if not exists public.accounts (
  id text primary key,
  status text not null default 'active',
  data jsonb not null
);

create table if not exists public.users (
  id text primary key,
  account_id text references public.accounts(id) on delete cascade,
  username text not null unique,
  friend_code text not null unique,
  revision bigint not null default 1,
  data jsonb not null
);
alter table public.users add column if not exists revision bigint not null default 1;
alter table public.users add column if not exists account_id text;

insert into public.accounts(id, status, data)
select
  coalesce(data->>'account_id', 'account_' || id),
  'active',
  jsonb_build_object(
    'id', coalesce(data->>'account_id', 'account_' || id),
    'auth_provider', coalesce(data->>'auth_provider', 'local'),
    'provider_user_id', coalesce(data->>'provider_user_id', ''),
    'password_salt', coalesce(data->>'password_salt', ''),
    'password_hash', coalesce(data->>'password_hash', ''),
    'phone', coalesce(data->>'phone', ''),
    'age_group', coalesce(data->>'age_group', ''),
    'gender', coalesce(data->>'gender', ''),
    'created_at', coalesce(data->>'created_at', timezone('utc', now())::text),
    'status', 'active'
  )
from public.users
where account_id is null
   or not (data ? 'account_id')
   or data ?| array['password_salt', 'password_hash', 'phone', 'age_group', 'gender']
on conflict (id) do update set
  data=public.accounts.data || excluded.data;

update public.users set
  account_id=coalesce(account_id, data->>'account_id', 'account_' || id),
  data=jsonb_set(
    data - 'password_salt' - 'password_hash' - 'phone' - 'age_group' - 'gender',
    '{account_id}',
    to_jsonb(coalesce(account_id, data->>'account_id', 'account_' || id))
  )
where account_id is null
   or not (data ? 'account_id')
   or data ?| array['password_salt', 'password_hash', 'phone', 'age_group', 'gender'];

alter table public.users alter column account_id set not null;
do $$ begin
  if not exists(select 1 from pg_constraint where conname='users_account_id_fkey') then
    alter table public.users add constraint users_account_id_fkey
      foreign key(account_id) references public.accounts(id) on delete cascade;
  end if;
end $$;

do $$
declare over_limit_account text;
begin
  select account_id into over_limit_account
  from public.users
  where coalesce(data->>'disabled_at', '')=''
  group by account_id
  having count(*) > 3
  limit 1;
  if over_limit_account is not null then
    raise exception 'account identity limit already exceeded for account %', over_limit_account;
  end if;
end;
$$;

create or replace function public.colorless_enforce_identity_limit()
returns trigger language plpgsql set search_path = public as $$
begin
  if coalesce(new.data->>'disabled_at', '')<>'' then
    return new;
  end if;
  if tg_op = 'UPDATE' and new.account_id = old.account_id and coalesce(old.data->>'disabled_at', '')='' then
    return new;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(new.account_id, 0));
  if (select count(*) from users where account_id=new.account_id and id <> new.id
      and coalesce(data->>'disabled_at', '')='') >= 3 then
    raise exception 'account identity limit exceeded';
  end if;
  return new;
end;
$$;
drop trigger if exists users_account_identity_limit on public.users;
create trigger users_account_identity_limit
before insert or update of account_id, data on public.users
for each row execute function public.colorless_enforce_identity_limit();
create index if not exists users_account_idx on public.users(account_id, id);

create table if not exists public.profile_art (
  user_id text primary key references public.users(id) on delete cascade,
  version bigint not null,
  pixels_rgb bytea not null check (octet_length(pixels_rgb) = 3072),
  updated_at double precision not null default extract(epoch from now())
);

-- Older revisions declared this epoch value as timestamptz. Upgrade those
-- databases without changing the instant represented by existing rows.
do $$
declare profile_art_updated_at_type text;
begin
  select data_type into profile_art_updated_at_type
  from information_schema.columns
  where table_schema='public' and table_name='profile_art' and column_name='updated_at';

  if profile_art_updated_at_type in ('timestamp with time zone', 'timestamp without time zone') then
    execute 'alter table public.profile_art alter column updated_at drop default';
    execute 'alter table public.profile_art alter column updated_at type double precision using extract(epoch from updated_at)';
  elsif profile_art_updated_at_type <> 'double precision' then
    raise exception 'Unsupported public.profile_art.updated_at type: %', profile_art_updated_at_type;
  end if;

  execute 'alter table public.profile_art alter column updated_at set default extract(epoch from now())';
end;
$$;

create table if not exists public.app_migrations (
  key text primary key,
  version integer not null,
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.social_accounts (
  provider text not null,
  provider_user_id text not null,
  user_id text not null references public.users(id) on delete cascade,
  account_id text references public.accounts(id) on delete cascade,
  primary key (provider, provider_user_id)
);
alter table public.social_accounts add column if not exists account_id text references public.accounts(id) on delete cascade;
update public.social_accounts set account_id=users.account_id
from public.users where social_accounts.user_id=users.id and social_accounts.account_id is null;
alter table public.social_accounts alter column account_id set not null;

create table if not exists public.friendships (
  user_low_id text not null references public.users(id) on delete cascade,
  user_high_id text not null references public.users(id) on delete cascade,
  created_at timestamptz not null,
  primary key (user_low_id, user_high_id),
  check (user_low_id < user_high_id)
);

create table if not exists public.rooms (
  id text primary key,
  kind text not null,
  created_by text not null,
  updated_at timestamptz not null,
  revision bigint not null default 1,
  direct_key text,
  data jsonb not null
);
alter table public.rooms add column if not exists revision bigint not null default 1;
alter table public.rooms add column if not exists direct_key text;
create unique index if not exists rooms_direct_key_unique_idx
  on public.rooms(direct_key) where direct_key is not null;
with direct_keys as (
  select rooms.id, string_agg(member.value, ':' order by member.value) as direct_key
  from public.rooms
  cross join lateral jsonb_array_elements_text(coalesce(rooms.data->'participant_ids', '[]'::jsonb)) member(value)
  where rooms.kind='direct' and rooms.direct_key is null
  group by rooms.id
  having count(*)=2
), ranked_keys as (
  select id, direct_key, row_number() over(partition by direct_key order by id) as key_rank
  from direct_keys
)
update public.rooms as rooms set direct_key=ranked_keys.direct_key
from ranked_keys where rooms.id=ranked_keys.id and ranked_keys.key_rank=1;

create table if not exists public.room_members (
  room_id text not null references public.rooms(id) on delete cascade,
  user_id text not null references public.users(id) on delete cascade,
  primary key (room_id, user_id)
);
create index if not exists room_members_user_room_idx on public.room_members(user_id, room_id);

create table if not exists public.messages (
  sequence bigint generated always as identity primary key,
  id text not null unique,
  room_id text not null references public.rooms(id) on delete cascade,
  sender_id text not null references public.users(id) on delete restrict,
  sender_username text not null,
  client_message_id text,
  created_at timestamptz not null,
  data jsonb not null
);
create index if not exists messages_room_sequence_idx on public.messages(room_id, sequence desc);
create unique index if not exists messages_client_id_unique_idx
  on public.messages(room_id, sender_id, client_message_id)
  where client_message_id is not null;

create table if not exists public.message_reactions (
  message_id text not null references public.messages(id) on delete cascade,
  user_id text not null references public.users(id) on delete cascade,
  emoji text not null check (emoji in ('👍', '❤️', '😂', '😮', '😢', '🙏')),
  created_at timestamptz not null,
  primary key (message_id, user_id, emoji)
);
create index if not exists message_reactions_message_idx
  on public.message_reactions(message_id, emoji, created_at);

create table if not exists public.read_positions (
  room_id text not null references public.rooms(id) on delete cascade,
  user_id text not null references public.users(id) on delete cascade,
  message_id text not null references public.messages(id) on delete cascade,
  primary key (room_id, user_id)
);

create table if not exists public.sessions (
  token_hash text primary key,
  user_id text not null references public.users(id) on delete cascade,
  account_id text references public.accounts(id) on delete cascade,
  active_user_id text references public.users(id) on delete cascade,
  created_at double precision not null,
  expires_at double precision not null
);
alter table public.sessions add column if not exists account_id text references public.accounts(id) on delete cascade;
alter table public.sessions add column if not exists active_user_id text references public.users(id) on delete cascade;
update public.sessions set account_id=users.account_id, active_user_id=users.id
from public.users where sessions.user_id=users.id and (sessions.account_id is null or sessions.active_user_id is null);
alter table public.sessions alter column account_id set not null;
alter table public.sessions alter column active_user_id set not null;
create index if not exists sessions_expires_idx on public.sessions(expires_at);

create table if not exists public.shorts_feeds (
  user_id text primary key references public.users(id) on delete cascade,
  next_cursor text not null default ''
);

create table if not exists public.shorts_seen (
  user_id text not null references public.users(id) on delete cascade,
  video_id text not null,
  seen_order bigint not null,
  primary key (user_id, video_id)
);
create index if not exists shorts_seen_user_order_idx on public.shorts_seen(user_id, seen_order desc);

create table if not exists public.shorts_catalog (
  video_id text primary key,
  source text not null,
  rank_score double precision not null default 0,
  discovered_at double precision not null,
  last_seen_at double precision not null,
  expires_at double precision not null,
  data jsonb not null
);
create index if not exists shorts_catalog_feed_idx
  on public.shorts_catalog(expires_at desc, rank_score desc, last_seen_at desc, video_id);
create index if not exists shorts_catalog_expiry_idx on public.shorts_catalog(expires_at);

create table if not exists public.shorts_collection_state (
  source text primary key,
  owner_instance_id text not null default '',
  lease_until double precision not null default 0,
  next_job_index integer not null default 0,
  failure_count integer not null default 0,
  circuit_open_until double precision not null default 0,
  last_success_at double precision not null default 0,
  last_attempt_at double precision not null default 0,
  last_error text not null default '',
  quota_window_start double precision not null default 0,
  quota_used integer not null default 0
);

create table if not exists public.realtime_events (
  sequence bigint generated always as identity primary key,
  event_id text not null unique,
  event_type text not null,
  room_id text not null default '',
  occurred_at timestamptz not null,
  origin_instance_id text not null,
  recipients text[] not null,
  data jsonb not null
);
create index if not exists realtime_events_occurred_idx on public.realtime_events(occurred_at);
create index if not exists realtime_events_recipients_idx on public.realtime_events using gin(recipients);

create table if not exists public.presence_leases (
  lease_id text primary key,
  instance_id text not null,
  username text not null,
  active_room_id text not null default '',
  emoji text not null default '',
  updated_at timestamptz not null,
  expires_at timestamptz not null
);
create index if not exists presence_leases_expiry_idx on public.presence_leases(expires_at);
create index if not exists presence_leases_user_expiry_idx on public.presence_leases(username, expires_at);

alter table public.accounts enable row level security;
alter table public.users enable row level security;
alter table public.profile_art enable row level security;
alter table public.app_migrations enable row level security;
alter table public.social_accounts enable row level security;
alter table public.friendships enable row level security;
alter table public.rooms enable row level security;
alter table public.room_members enable row level security;
alter table public.messages enable row level security;
alter table public.message_reactions enable row level security;
alter table public.read_positions enable row level security;
alter table public.sessions enable row level security;
alter table public.shorts_feeds enable row level security;
alter table public.shorts_seen enable row level security;
alter table public.shorts_catalog enable row level security;
alter table public.shorts_collection_state enable row level security;
alter table public.realtime_events enable row level security;
alter table public.presence_leases enable row level security;

drop function if exists public.colorless_sync_user(jsonb);
create or replace function public.colorless_sync_user(user_data jsonb)
returns bigint language plpgsql security definer set search_path = public as $$
declare expected_revision bigint := coalesce((user_data->>'_revision')::bigint, 0); new_revision bigint;
begin
  if expected_revision = 0 then
    insert into users(id, account_id, username, friend_code, revision, data)
    values (
      user_data->>'id', user_data->>'account_id', user_data->>'username', user_data->>'friend_code',
      1, user_data || jsonb_build_object('_revision', 1)
    ) on conflict(id) do nothing returning revision into new_revision;
  else
    update users set
      account_id=user_data->>'account_id', username=user_data->>'username', friend_code=user_data->>'friend_code',
      revision=revision+1,
      data=user_data || jsonb_build_object('_revision', revision+1)
    where id=user_data->>'id' and revision=expected_revision
    returning revision into new_revision;
  end if;
  if new_revision is null then return null; end if;
  delete from social_accounts where user_id = user_data->>'id';
  if coalesce(user_data->>'provider_user_id', '') <> '' then
    insert into social_accounts(provider, provider_user_id, user_id, account_id)
    values (coalesce(user_data->>'auth_provider', 'local'), user_data->>'provider_user_id', user_data->>'id', user_data->>'account_id')
    on conflict (provider, provider_user_id) do update set user_id = excluded.user_id, account_id=excluded.account_id;
  end if;
  return new_revision;
end;
$$;

drop function if exists public.colorless_sync_room(jsonb);
create or replace function public.colorless_sync_room(room_data jsonb)
returns bigint language plpgsql security definer set search_path = public as $$
declare
  member_id text; read_entry record;
  expected_revision bigint := coalesce((room_data->>'_revision')::bigint, 0);
  new_revision bigint; direct_room_key text;
begin
  if room_data->>'kind' = 'direct' and jsonb_array_length(coalesce(room_data->'participant_ids', '[]'::jsonb)) = 2 then
    select string_agg(value, ':' order by value) into direct_room_key
    from jsonb_array_elements_text(room_data->'participant_ids');
  end if;
  if expected_revision = 0 then
    insert into rooms(id, kind, created_by, updated_at, revision, direct_key, data)
    values (
      room_data->>'id', coalesce(room_data->>'kind', 'group'),
      coalesce(room_data->>'created_by', ''),
      coalesce(room_data->>'updated_at', timezone('utc', now())::text)::timestamptz,
      1, direct_room_key, room_data || jsonb_build_object('_revision', 1)
    ) on conflict(id) do nothing returning revision into new_revision;
  else
    update rooms set
      kind=coalesce(room_data->>'kind', 'group'),
      created_by=coalesce(room_data->>'created_by', ''),
      updated_at=coalesce(room_data->>'updated_at', timezone('utc', now())::text)::timestamptz,
      revision=revision+1, direct_key=direct_room_key,
      data=room_data || jsonb_build_object('_revision', revision+1)
    where id=room_data->>'id' and revision=expected_revision
    returning revision into new_revision;
  end if;
  if new_revision is null then return null; end if;
  delete from room_members where room_id = room_data->>'id';
  for member_id in select jsonb_array_elements_text(coalesce(room_data->'participant_ids', '[]'::jsonb)) loop
    insert into room_members(room_id, user_id) values (room_data->>'id', member_id);
  end loop;
  delete from read_positions where room_id = room_data->>'id';
  for read_entry in select key, value from jsonb_each_text(coalesce(room_data->'last_read_by', '{}'::jsonb)) loop
    if exists(select 1 from messages where id = read_entry.value) then
      insert into read_positions(room_id, user_id, message_id)
      values (room_data->>'id', read_entry.key, read_entry.value);
    end if;
  end loop;
  return new_revision;
end;
$$;

drop function if exists public.colorless_insert_message_v2(jsonb, text, jsonb, integer);
create or replace function public.colorless_lock_active_identity(actor_user_id text)
returns boolean language plpgsql security definer set search_path=public as $$
begin
  perform 1 from users u join accounts a on a.id=u.account_id
    where u.id=actor_user_id and a.status='active' and coalesce(u.data->>'disabled_at','')=''
    for share of u,a;
  return found;
end;
$$;
revoke all on function public.colorless_lock_active_identity(text) from public,anon,authenticated;
grant execute on function public.colorless_lock_active_identity(text) to service_role;

create or replace function public.colorless_insert_message_v2(
  message_data jsonb, sender_user_id text, room_data jsonb, keep_count integer
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  inserted_count integer;
  new_revision bigint;
  message_time timestamptz;
  target_room_kind text;
  reply_target_data jsonb;
  recipient_usernames jsonb;
begin
  select kind into target_room_kind
  from rooms
  where id=message_data->>'room_id'
  for update;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if not colorless_lock_active_identity(sender_user_id) then
    return jsonb_build_object('error', 'forbidden');
  end if;
  if target_room_kind <> 'public' then
    perform 1 from room_members
    where room_id=message_data->>'room_id' and user_id=sender_user_id
    for key share;
    if not found then
      return jsonb_build_object('error', 'forbidden');
    end if;
  end if;
  if nullif(message_data->>'reply_to_message_id', '') is not null then
    perform 1 from messages
    where room_id=message_data->>'room_id'
      and id=message_data->>'reply_to_message_id'
    for key share;
    if not found then
      return jsonb_build_object('error', 'reply_not_found');
    end if;
  end if;
  message_time := (message_data->>'timestamp')::timestamptz;
  insert into messages(id, room_id, sender_id, sender_username, client_message_id, created_at, data)
  values (
    message_data->>'id', message_data->>'room_id', sender_user_id,
    coalesce(message_data->>'username', ''), nullif(message_data->>'client_message_id', ''),
    message_time, message_data
  ) on conflict do nothing;
  get diagnostics inserted_count = row_count;
  if inserted_count = 0 then
    return jsonb_build_object('error', 'conflict');
  end if;
  update rooms set
    updated_at=message_time,
    data=jsonb_set(
      jsonb_set(data, '{updated_at}', to_jsonb(message_data->>'timestamp')),
      '{_revision}', to_jsonb(revision+1)
    ),
    revision=revision+1
  where id=message_data->>'room_id'
  returning revision into new_revision;
  if new_revision is null then
    raise exception 'message room does not exist';
  end if;
  if keep_count > 0 then
    delete from messages where room_id = message_data->>'room_id' and sequence not in (
      select sequence from messages where room_id = message_data->>'room_id' order by sequence desc limit keep_count
    );
  end if;
  if nullif(message_data->>'reply_to_message_id', '') is not null then
    select data into reply_target_data
    from messages
    where room_id=message_data->>'room_id'
      and id=message_data->>'reply_to_message_id';
  end if;
  if target_room_kind = 'public' then
    select coalesce(jsonb_agg(username order by username), '[]'::jsonb)
    into recipient_usernames
    from users;
  else
    select coalesce(jsonb_agg(users.username order by users.username), '[]'::jsonb)
    into recipient_usernames
    from room_members
    join users on users.id=room_members.user_id
    where room_members.room_id=message_data->>'room_id';
  end if;
  return jsonb_build_object(
    'revision', new_revision,
    'reply_to_message', reply_target_data,
    'reactions', '[]'::jsonb,
    'recipient_usernames', recipient_usernames
  );
end;
$$;

-- Keep the original bigint contract alive while the schema-first deployment is
-- rolling. Existing Render instances still call this function until the new
-- application release starts using colorless_insert_message_v2.
drop function if exists public.colorless_insert_message(jsonb, text, jsonb, integer);
create or replace function public.colorless_insert_message(
  message_data jsonb, sender_user_id text, room_data jsonb, keep_count integer
) returns bigint language plpgsql security definer set search_path = public as $$
declare result jsonb;
begin
  result := public.colorless_insert_message_v2(
    message_data,
    sender_user_id,
    room_data,
    keep_count
  );
  return coalesce((result->>'revision')::bigint, 0);
end;
$$;

create or replace function public.colorless_latest_messages(room_ids text[])
returns table(room_id text, data jsonb)
language sql stable security definer set search_path = public as $$
  select distinct on (messages.room_id) messages.room_id, messages.data
  from messages
  where messages.room_id = any(room_ids)
  order by messages.room_id, messages.sequence desc;
$$;

drop function if exists public.colorless_unread_counts(jsonb);
create or replace function public.colorless_unread_counts(room_viewers jsonb)
returns jsonb
language sql stable security definer set search_path = public as $$
  with requested as (
    select distinct
      item->>'room_id' as room_id,
      item->>'viewer_user_id' as viewer_user_id
    from jsonb_array_elements(coalesce(room_viewers, '[]'::jsonb)) as item
    where nullif(item->>'room_id', '') is not null
      and nullif(item->>'viewer_user_id', '') is not null
  ), read_sequences as (
    select
      requested.room_id,
      requested.viewer_user_id,
      coalesce(read_message.sequence, 0) as last_read_sequence
    from requested
    left join read_positions
      on read_positions.room_id=requested.room_id
      and read_positions.user_id=requested.viewer_user_id
    left join messages as read_message
      on read_message.id=read_positions.message_id
      and read_message.room_id=requested.room_id
  ), counts as (
    select
      read_sequences.room_id,
      count(messages.id)::bigint as unread_count
    from read_sequences
    left join messages
      on messages.room_id=read_sequences.room_id
      and messages.sequence>read_sequences.last_read_sequence
      and messages.sender_id<>read_sequences.viewer_user_id
    group by read_sequences.room_id
  )
  select coalesce(jsonb_object_agg(counts.room_id, counts.unread_count), '{}'::jsonb)
  from counts;
$$;

drop function if exists public.colorless_edit_message(text, text, text, text, text);
create or replace function public.colorless_edit_message(
  message_room_id text, target_message_id text, editor_user_id text,
  edited_text text, edited_timestamp text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  target_sender_id text;
  target_room_kind text;
  stored_message jsonb;
  updated_message jsonb;
  reaction_snapshot jsonb;
  reply_target_data jsonb;
  recipient_usernames jsonb;
  next_mutation_revision bigint;
begin
  select target.sender_id, target_room.kind, target.data
  into target_sender_id, target_room_kind, stored_message
  from messages as target
  join rooms as target_room on target_room.id=target.room_id
  where target.room_id=message_room_id and target.id=target_message_id
  for update of target;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if target_sender_id <> editor_user_id then
    return jsonb_build_object('error', 'forbidden');
  end if;
  if not colorless_lock_active_identity(editor_user_id) then
    return jsonb_build_object('error', 'forbidden');
  end if;
  if target_room_kind <> 'public' then
    perform 1 from room_members
    where room_id=message_room_id and user_id=editor_user_id
    for key share;
    if not found then
      return jsonb_build_object('error', 'forbidden');
    end if;
  end if;
  if edited_text = '' and jsonb_typeof(stored_message->'attachment') is distinct from 'object' then
    return jsonb_build_object('error', 'empty');
  end if;

  next_mutation_revision := coalesce((stored_message->>'mutation_revision')::bigint, 0) + 1;
  update messages
  set data=jsonb_set(
    jsonb_set(
      jsonb_set(data, '{text}', to_jsonb(edited_text)),
      '{edited_at}', to_jsonb(edited_timestamp)
    ),
    '{mutation_revision}', to_jsonb(next_mutation_revision)
  )
  where room_id=message_room_id and id=target_message_id
  returning data into updated_message;
  select coalesce(
    jsonb_agg(
      jsonb_build_object('message_id', message_id, 'user_id', user_id, 'emoji', emoji)
      order by emoji, created_at, user_id
    ),
    '[]'::jsonb
  ) into reaction_snapshot
  from message_reactions
  where message_id=target_message_id;
  if nullif(stored_message->>'reply_to_message_id', '') is not null then
    select data into reply_target_data
    from messages
    where room_id=message_room_id
      and id=stored_message->>'reply_to_message_id';
  end if;
  if target_room_kind = 'public' then
    select coalesce(jsonb_agg(username order by username), '[]'::jsonb)
    into recipient_usernames
    from users;
  else
    select coalesce(jsonb_agg(users.username order by users.username), '[]'::jsonb)
    into recipient_usernames
    from room_members
    join users on users.id=room_members.user_id
    where room_members.room_id=message_room_id;
  end if;
  return jsonb_build_object(
    'message', updated_message,
    'reactions', reaction_snapshot,
    'reply_to_message', reply_target_data,
    'recipient_usernames', recipient_usernames
  );
end;
$$;

drop function if exists public.colorless_toggle_message_reaction(text, text, text, text, timestamptz);
drop function if exists public.colorless_toggle_message_reaction(text, text, text, text, timestamptz, boolean);
create or replace function public.colorless_toggle_message_reaction(
  reaction_room_id text, reaction_message_id text, reaction_user_id text,
  reaction_emoji text, reaction_created_at timestamptz, desired_reacted boolean default null
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  target_room_kind text;
  stored_message jsonb;
  updated_message jsonb;
  reaction_snapshot jsonb;
  reply_target_data jsonb;
  recipient_usernames jsonb;
  reaction_count bigint;
  is_reacted boolean;
  was_reacted boolean;
  reaction_changed boolean;
  next_mutation_revision bigint;
begin
  select target_room.kind, target.data
  into target_room_kind, stored_message
  from messages as target
  join rooms as target_room on target_room.id=target.room_id
  where target.room_id=reaction_room_id and target.id=reaction_message_id
  for update of target;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if not colorless_lock_active_identity(reaction_user_id) then
    return jsonb_build_object('error', 'forbidden');
  end if;
  if target_room_kind <> 'public' then
    perform 1 from room_members
    where room_id=reaction_room_id and user_id=reaction_user_id
    for key share;
    if not found then
      return jsonb_build_object('error', 'forbidden');
    end if;
  end if;

  select exists(
    select 1 from message_reactions
    where message_id=reaction_message_id and user_id=reaction_user_id and emoji=reaction_emoji
  ) into was_reacted;
  is_reacted := coalesce(desired_reacted, not was_reacted);
  reaction_changed := is_reacted <> was_reacted;
  if reaction_changed and is_reacted then
    insert into message_reactions(message_id, user_id, emoji, created_at)
    values (reaction_message_id, reaction_user_id, reaction_emoji, reaction_created_at);
  elsif reaction_changed then
    delete from message_reactions
    where message_id=reaction_message_id and user_id=reaction_user_id and emoji=reaction_emoji;
  end if;
  select count(*) into reaction_count
  from message_reactions
  where message_id=reaction_message_id and emoji=reaction_emoji;
  next_mutation_revision := coalesce((stored_message->>'mutation_revision')::bigint, 0);
  if reaction_changed then
    next_mutation_revision := next_mutation_revision + 1;
    update messages
    set data=jsonb_set(data, '{mutation_revision}', to_jsonb(next_mutation_revision))
    where room_id=reaction_room_id and id=reaction_message_id
    returning data into updated_message;
  else
    updated_message := jsonb_set(
      stored_message,
      '{mutation_revision}',
      to_jsonb(next_mutation_revision)
    );
  end if;
  select coalesce(
    jsonb_agg(
      jsonb_build_object('message_id', message_id, 'user_id', user_id, 'emoji', emoji)
      order by emoji, created_at, user_id
    ),
    '[]'::jsonb
  ) into reaction_snapshot
  from message_reactions
  where message_id=reaction_message_id;
  if nullif(stored_message->>'reply_to_message_id', '') is not null then
    select data into reply_target_data
    from messages
    where room_id=reaction_room_id
      and id=stored_message->>'reply_to_message_id';
  end if;
  if target_room_kind = 'public' then
    select coalesce(jsonb_agg(username order by username), '[]'::jsonb)
    into recipient_usernames
    from users;
  else
    select coalesce(jsonb_agg(users.username order by users.username), '[]'::jsonb)
    into recipient_usernames
    from room_members
    join users on users.id=room_members.user_id
    where room_members.room_id=reaction_room_id;
  end if;
  return jsonb_build_object(
    'reacted', is_reacted,
    'count', reaction_count,
    'mutation_revision', next_mutation_revision,
    'changed', reaction_changed,
    'message', updated_message,
    'reactions', reaction_snapshot,
    'reply_to_message', reply_target_data,
    'recipient_usernames', recipient_usernames
  );
end;
$$;

drop function if exists public.colorless_delete_message(text, text, text);
create or replace function public.colorless_delete_message(
  message_room_id text, target_message_id text, deleting_user_id text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  target_sender_id text;
  target_room_kind text;
  target_message_data jsonb;
  target_message_sequence bigint;
  target_room_data jsonb;
  target_room_revision bigint;
  target_room_updated_at timestamptz;
  previous_message_id text;
  latest_message_data jsonb;
  deleted_attachment_url text;
  attachment_still_used boolean;
  next_room_updated_at_text text;
  next_room_updated_at timestamptz;
  next_last_read_by jsonb;
  next_room_revision bigint;
  next_room_data jsonb;
  recipient_usernames jsonb;
begin
  select kind, data, revision, updated_at
  into target_room_kind, target_room_data, target_room_revision, target_room_updated_at
  from rooms
  where id=message_room_id
  for update;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if target_room_kind <> 'public' then
    perform 1 from room_members
    where room_id=message_room_id and user_id=deleting_user_id
    for key share;
    if not found then
      return jsonb_build_object('error', 'forbidden');
    end if;
  end if;

  select sender_id, data, sequence
  into target_sender_id, target_message_data, target_message_sequence
  from messages
  where room_id=message_room_id and id=target_message_id
  for update;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if target_sender_id <> deleting_user_id then
    return jsonb_build_object('error', 'forbidden');
  end if;
  if not colorless_lock_active_identity(deleting_user_id) then
    return jsonb_build_object('error', 'forbidden');
  end if;

  select id into previous_message_id
  from messages
  where room_id=message_room_id and sequence<target_message_sequence
  order by sequence desc
  limit 1;
  if previous_message_id is null then
    delete from read_positions
    where room_id=message_room_id and message_id=target_message_id;
  else
    update read_positions
    set message_id=previous_message_id
    where room_id=message_room_id and message_id=target_message_id;
  end if;
  delete from messages
  where room_id=message_room_id and id=target_message_id and sender_id=deleting_user_id;

  deleted_attachment_url := target_message_data#>>'{attachment,url}';
  select exists(
    select 1 from messages
    where room_id=message_room_id
      and data#>>'{attachment,url}'=deleted_attachment_url
  ) into attachment_still_used;
  select data into latest_message_data
  from messages
  where room_id=message_room_id
  order by sequence desc
  limit 1;
  next_room_updated_at_text := coalesce(
    nullif(latest_message_data->>'timestamp', ''),
    nullif(target_room_data->>'created_at', ''),
    target_room_updated_at::text
  );
  next_room_updated_at := next_room_updated_at_text::timestamptz;
  select coalesce(jsonb_object_agg(user_id, message_id), '{}'::jsonb)
  into next_last_read_by
  from read_positions
  where room_id=message_room_id;
  next_room_revision := target_room_revision + 1;
  next_room_data := jsonb_set(
    jsonb_set(
      jsonb_set(
        target_room_data,
        '{updated_at}',
        to_jsonb(next_room_updated_at_text)
      ),
      '{last_read_by}',
      next_last_read_by
    ),
    '{_revision}',
    to_jsonb(next_room_revision)
  );
  update rooms
  set updated_at=next_room_updated_at,
      revision=next_room_revision,
      data=next_room_data
  where id=message_room_id;
  if target_room_kind = 'public' then
    select coalesce(jsonb_agg(username order by username), '[]'::jsonb)
    into recipient_usernames
    from users;
  else
    select coalesce(jsonb_agg(users.username order by users.username), '[]'::jsonb)
    into recipient_usernames
    from room_members
    join users on users.id=room_members.user_id
    where room_members.room_id=message_room_id;
  end if;
  return jsonb_build_object(
    'deleted', true,
    'message', target_message_data,
    'room', next_room_data,
    'latest_message', latest_message_data,
    'attachment_still_used', attachment_still_used,
    'revision', next_room_revision,
    'recipient_usernames', recipient_usernames
  );
end;
$$;

create or replace function public.colorless_create_session(
  session_token_hash text, session_user_id text, created_epoch double precision,
  expires_epoch double precision, max_session_count integer
) returns void language plpgsql security definer set search_path = public as $$
begin
  delete from sessions where expires_at <= created_epoch;
  insert into sessions(token_hash, user_id, created_at, expires_at)
  values (session_token_hash, session_user_id, created_epoch, expires_epoch)
  on conflict (token_hash) do update set user_id=excluded.user_id, created_at=excluded.created_at, expires_at=excluded.expires_at;
  delete from sessions where token_hash in (
    select token_hash from sessions order by created_at
    limit greatest(0, (select count(*) from sessions) - max_session_count)
  );
end;
$$;

create or replace function public.colorless_session_username(session_token_hash text, now_epoch double precision)
returns text language plpgsql security definer set search_path = public as $$
declare result text;
begin
  delete from sessions where expires_at <= now_epoch;
  select users.username into result from sessions join users on users.id=sessions.user_id
  where sessions.token_hash=session_token_hash;
  return result;
end;
$$;

create or replace function public.colorless_create_account_session(
  session_token_hash text, session_account_id text, session_active_user_id text,
  created_epoch double precision, expires_epoch double precision, max_session_count integer
) returns void language plpgsql security definer set search_path = public as $$
begin
  if not exists(select 1 from users where id=session_active_user_id and account_id=session_account_id) then
    raise exception 'identity is not owned by account';
  end if;
  delete from sessions where expires_at <= created_epoch;
  insert into sessions(token_hash, user_id, account_id, active_user_id, created_at, expires_at)
  values (session_token_hash, session_active_user_id, session_account_id, session_active_user_id, created_epoch, expires_epoch)
  on conflict (token_hash) do update set
    user_id=excluded.user_id, account_id=excluded.account_id, active_user_id=excluded.active_user_id,
    created_at=excluded.created_at, expires_at=excluded.expires_at;
  delete from sessions where token_hash in (
    select token_hash from sessions order by created_at
    limit greatest(0, (select count(*) from sessions) - max_session_count)
  );
end;
$$;

create or replace function public.colorless_account_session_username(session_token_hash text, now_epoch double precision)
returns text language plpgsql security definer set search_path = public as $$
declare result text;
begin
  delete from sessions where expires_at <= now_epoch;
  select users.username into result
  from sessions
  join accounts on accounts.id=sessions.account_id and accounts.status='active'
  join users on users.id=sessions.active_user_id and users.account_id=accounts.id
  where sessions.token_hash=session_token_hash and coalesce(users.data->>'disabled_at', '')='';
  return result;
end;
$$;

create or replace function public.colorless_switch_session_identity(
  session_token_hash text, session_account_id text, target_user_id text
) returns boolean language plpgsql security definer set search_path = public as $$
declare changed_count integer;
begin
  update sessions set user_id=target_user_id, active_user_id=target_user_id
  where token_hash=session_token_hash and account_id=session_account_id
    and expires_at>extract(epoch from now())
    and exists(select 1 from accounts where id=session_account_id and status='active')
    and exists(
      select 1 from users
      where id=target_user_id and account_id=session_account_id
        and coalesce(data->>'disabled_at', '')=''
    );
  get diagnostics changed_count = row_count;
  return changed_count = 1;
end;
$$;

create or replace function public.colorless_disable_identity(
  owner_user_id text, target_user_id text, disabled_timestamp text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare owner_account text; target users%rowtype;
begin
  select u.account_id into owner_account from users u join accounts a on a.id=u.account_id
    where u.id=owner_user_id and a.status='active' and coalesce(u.data->>'disabled_at', '')='';
  if owner_account is null then return jsonb_build_object('error','forbidden'); end if;
  perform pg_advisory_xact_lock(hashtextextended(owner_account, 0));
  -- Recheck under the account lock so concurrent deactivations cannot remove every ID.
  if not exists(select 1 from users where id=owner_user_id and coalesce(data->>'disabled_at','')='') then
    return jsonb_build_object('error','forbidden');
  end if;
  select * into target from users where id=target_user_id for update;
  if not found or target.account_id<>owner_account then return jsonb_build_object('error','forbidden'); end if;
  if owner_user_id=target_user_id then return jsonb_build_object('error','switch_first'); end if;
  update users set data=jsonb_set(data,'{disabled_at}',to_jsonb(disabled_timestamp)), revision=revision+1
    where id=target_user_id;
  update sessions set user_id=owner_user_id,active_user_id=owner_user_id
    where account_id=owner_account and active_user_id=target_user_id;
  delete from presence_leases where username=target.username;
  return jsonb_build_object('disabled',true);
end;
$$;
revoke all on function public.colorless_disable_identity(text,text,text) from public, anon, authenticated;
grant execute on function public.colorless_disable_identity(text,text,text) to service_role;

create or replace function public.colorless_save_shorts_feed(feed_user_id text, seen_video_ids text[], cursor_value text)
returns void language plpgsql security definer set search_path = public as $$
begin
  insert into shorts_feeds(user_id, next_cursor) values(feed_user_id, cursor_value)
  on conflict(user_id) do update set next_cursor=excluded.next_cursor;
  delete from shorts_seen where user_id=feed_user_id;
  insert into shorts_seen(user_id, video_id, seen_order)
  select feed_user_id, video_id, ordinal - 1 from unnest(seen_video_ids) with ordinality as item(video_id, ordinal);
end;
$$;

create or replace function public.colorless_acquire_shorts_collection(
  collector_owner text, now_epoch double precision, lease_seconds integer,
  requested_quota integer, daily_quota integer
) returns jsonb language plpgsql security definer set search_path = public as $$
declare current_state shorts_collection_state%rowtype;
begin
  insert into shorts_collection_state(source, quota_window_start)
  values('youtube', now_epoch) on conflict(source) do nothing;
  select * into current_state from shorts_collection_state where source='youtube' for update;
  if now_epoch-current_state.quota_window_start >= 86400 then
    current_state.quota_window_start := now_epoch;
    current_state.quota_used := 0;
  end if;
  if (current_state.lease_until>now_epoch and current_state.owner_instance_id<>collector_owner)
     or current_state.circuit_open_until>now_epoch
     or current_state.quota_used+requested_quota>daily_quota then
    return null;
  end if;
  update shorts_collection_state set
    owner_instance_id=collector_owner, lease_until=now_epoch+lease_seconds,
    last_attempt_at=now_epoch, quota_window_start=current_state.quota_window_start,
    quota_used=current_state.quota_used+requested_quota
  where source='youtube';
  return jsonb_build_object(
    'next_job_index', current_state.next_job_index,
    'quota_used', current_state.quota_used+requested_quota
  );
end;
$$;

create or replace function public.colorless_finish_shorts_collection(
  collector_owner text, now_epoch double precision, next_job_value integer,
  was_successful boolean, error_code text, circuit_seconds integer
) returns void language plpgsql security definer set search_path = public as $$
begin
  if was_successful then
    update shorts_collection_state set
      owner_instance_id='', lease_until=0, next_job_index=next_job_value,
      failure_count=0, circuit_open_until=0, last_success_at=now_epoch, last_error=''
    where source='youtube' and owner_instance_id=collector_owner;
  else
    update shorts_collection_state set
      owner_instance_id='', lease_until=0, failure_count=failure_count+1,
      circuit_open_until=case when failure_count+1>=3 or error_code like 'http-429%' or error_code like 'http-403%'
        then now_epoch+circuit_seconds else circuit_open_until end,
      last_error=left(error_code, 80)
    where source='youtube' and owner_instance_id=collector_owner;
  end if;
end;
$$;

create or replace function public.colorless_publish_event(
  event_data jsonb, recipient_usernames text[], source_instance_id text,
  occurred_epoch double precision, retention_count integer
) returns jsonb language plpgsql security definer set search_path = public as $$
declare event_sequence bigint; durable_event jsonb;
begin
  insert into realtime_events(
    event_id, event_type, room_id, occurred_at, origin_instance_id, recipients, data
  ) values (
    event_data->>'event_id', coalesce(event_data->>'type', ''),
    coalesce(event_data->>'roomId', ''), to_timestamp(occurred_epoch),
    source_instance_id, recipient_usernames, '{}'::jsonb
  ) on conflict(event_id) do nothing returning sequence into event_sequence;
  if event_sequence is null then
    select data into durable_event from realtime_events where event_id=event_data->>'event_id';
    return durable_event;
  end if;
  durable_event := event_data || jsonb_build_object(
    'revision', event_sequence,
    'occurred_at', to_char(to_timestamp(occurred_epoch) at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'origin_instance_id', source_instance_id
  );
  update realtime_events set data=durable_event where sequence=event_sequence;
  if retention_count > 0 and event_sequence % 100 = 0 then
    delete from realtime_events where sequence <= greatest(0, event_sequence-retention_count);
  end if;
  return durable_event;
end;
$$;

create or replace function public.colorless_presence_for_user(presence_username text)
returns jsonb language sql volatile security definer set search_path = public as $$
  select jsonb_build_object(
    'online', exists(
      select 1 from presence_leases
      where username=presence_username and expires_at>timezone('utc', now())
    ),
    'active_room_ids', coalesce((
      select jsonb_agg(active_room_id order by active_room_id)
      from (
        select distinct active_room_id from presence_leases
        where username=presence_username and expires_at>timezone('utc', now()) and active_room_id<>''
      ) rooms
    ), '[]'::jsonb),
    'emoji', coalesce((
      select emoji from presence_leases
      where username=presence_username and expires_at>timezone('utc', now()) and emoji<>''
      order by updated_at desc limit 1
    ), '')
  );
$$;

create or replace function public.colorless_presence_for_users(presence_usernames text[])
returns table(username text, presence jsonb)
language sql volatile security definer set search_path = public as $$
  select requested.username, colorless_presence_for_user(requested.username)
  from unnest(presence_usernames) as requested(username);
$$;

create or replace function public.colorless_touch_presence(
  presence_lease_id text, source_instance_id text, presence_username text,
  room_id_value text, emoji_value text, ttl_seconds integer
) returns jsonb language plpgsql security definer set search_path = public as $$
declare before_presence jsonb; current_presence jsonb; now_value timestamptz := timezone('utc', now());
begin
  before_presence := colorless_presence_for_user(presence_username);
  delete from presence_leases where expires_at<=now_value;
  insert into presence_leases(
    lease_id, instance_id, username, active_room_id, emoji, updated_at, expires_at
  ) values (
    presence_lease_id, source_instance_id, presence_username,
    room_id_value, emoji_value, now_value,
    now_value + make_interval(secs => greatest(1, ttl_seconds))
  ) on conflict(lease_id) do update set
    instance_id=excluded.instance_id, username=excluded.username,
    active_room_id=excluded.active_room_id, emoji=excluded.emoji,
    updated_at=excluded.updated_at, expires_at=excluded.expires_at;
  current_presence := colorless_presence_for_user(presence_username);
  return jsonb_build_object('presence', current_presence, 'changed', current_presence<>before_presence);
end;
$$;

create or replace function public.colorless_disconnect_presence(
  presence_lease_id text, presence_username text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare before_presence jsonb; current_presence jsonb;
begin
  before_presence := colorless_presence_for_user(presence_username);
  delete from presence_leases where lease_id=presence_lease_id;
  current_presence := colorless_presence_for_user(presence_username);
  return jsonb_build_object('presence', current_presence, 'changed', current_presence<>before_presence);
end;
$$;

create or replace function public.colorless_cleanup_presence()
returns jsonb language plpgsql security definer set search_path = public as $$
declare expired_usernames text[]; now_value timestamptz := timezone('utc', now());
begin
  select coalesce(array_agg(distinct username), '{}'::text[]) into expired_usernames
  from presence_leases where expires_at<=now_value;
  delete from presence_leases where expires_at<=now_value;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
      'username', username,
      'presence', colorless_presence_for_user(username)
    )) from unnest(expired_usernames) as expired(username)
  ), '[]'::jsonb);
end;
$$;

create or replace function public.colorless_storage_counts()
returns jsonb language sql security definer set search_path = public as $$
  select jsonb_build_object(
    'accounts', (select count(*) from accounts),
    'users', (select count(*) from users),
    'profile_art', (select count(*) from profile_art),
    'friendships', (select count(*) from friendships),
    'rooms', (select count(*) from rooms),
    'room_members', (select count(*) from room_members),
    'messages', (select count(*) from messages),
    'message_reactions', (select count(*) from message_reactions),
    'read_positions', (select count(*) from read_positions),
    'sessions', (select count(*) from sessions),
    'shorts_catalog', (select count(*) from shorts_catalog),
    'shorts_collection_state', (select count(*) from shorts_collection_state),
    'realtime_events', (select count(*) from realtime_events),
    'presence_leases', (select count(*) from presence_leases),
    'foreign_key_errors', 0
  );
$$;

create or replace function public.colorless_account_identity_integrity()
returns jsonb language sql security definer set search_path = public as $$
  select jsonb_build_object(
    'users_without_account', (select count(*) from users where account_id is null),
    'accounts_over_identity_limit', (
      select count(*) from (
        select account_id from users where coalesce(data->>'disabled_at','')=''
        group by account_id having count(*) > 3
      ) over_limit
    ),
    'sessions_without_account_identity', (
      select count(*) from sessions where account_id is null or active_user_id is null
    ),
    'sessions_with_foreign_identity', (
      select count(*)
      from sessions
      left join users on users.id=sessions.active_user_id and users.account_id=sessions.account_id
      where users.id is null
    )
  );
$$;

revoke all on table
  public.app_migrations, public.accounts, public.users, public.profile_art, public.social_accounts, public.friendships,
  public.rooms, public.room_members, public.messages, public.message_reactions, public.read_positions,
  public.sessions, public.shorts_feeds, public.shorts_seen, public.shorts_catalog,
  public.shorts_collection_state,
  public.realtime_events, public.presence_leases
from anon, authenticated;
grant select, insert, update, delete on table
  public.app_migrations, public.accounts, public.users, public.profile_art, public.social_accounts, public.friendships,
  public.rooms, public.room_members, public.messages, public.message_reactions, public.read_positions,
  public.sessions, public.shorts_feeds, public.shorts_seen, public.shorts_catalog,
  public.shorts_collection_state,
  public.realtime_events, public.presence_leases
to service_role;
grant usage, select on sequence public.messages_sequence_seq, public.realtime_events_sequence_seq to service_role;

revoke execute on function public.colorless_sync_user(jsonb) from public, anon, authenticated;
revoke execute on function public.colorless_enforce_identity_limit() from public, anon, authenticated;
revoke execute on function public.colorless_sync_room(jsonb) from public, anon, authenticated;
revoke execute on function public.colorless_insert_message(jsonb, text, jsonb, integer) from public, anon, authenticated;
revoke execute on function public.colorless_insert_message_v2(jsonb, text, jsonb, integer) from public, anon, authenticated;
revoke execute on function public.colorless_latest_messages(text[]) from public, anon, authenticated;
revoke execute on function public.colorless_unread_counts(jsonb) from public, anon, authenticated;
revoke execute on function public.colorless_edit_message(text, text, text, text, text) from public, anon, authenticated;
revoke execute on function public.colorless_toggle_message_reaction(text, text, text, text, timestamptz, boolean) from public, anon, authenticated;
revoke execute on function public.colorless_delete_message(text, text, text) from public, anon, authenticated;
revoke execute on function public.colorless_create_session(text, text, double precision, double precision, integer) from public, anon, authenticated;
revoke execute on function public.colorless_session_username(text, double precision) from public, anon, authenticated;
revoke execute on function public.colorless_create_account_session(text, text, text, double precision, double precision, integer) from public, anon, authenticated;
revoke execute on function public.colorless_account_session_username(text, double precision) from public, anon, authenticated;
revoke execute on function public.colorless_switch_session_identity(text, text, text) from public, anon, authenticated;
revoke execute on function public.colorless_save_shorts_feed(text, text[], text) from public, anon, authenticated;
revoke execute on function public.colorless_acquire_shorts_collection(text, double precision, integer, integer, integer) from public, anon, authenticated;
revoke execute on function public.colorless_finish_shorts_collection(text, double precision, integer, boolean, text, integer) from public, anon, authenticated;
revoke execute on function public.colorless_publish_event(jsonb, text[], text, double precision, integer) from public, anon, authenticated;
revoke execute on function public.colorless_presence_for_user(text) from public, anon, authenticated;
revoke execute on function public.colorless_presence_for_users(text[]) from public, anon, authenticated;
revoke execute on function public.colorless_touch_presence(text, text, text, text, text, integer) from public, anon, authenticated;
revoke execute on function public.colorless_disconnect_presence(text, text) from public, anon, authenticated;
revoke execute on function public.colorless_cleanup_presence() from public, anon, authenticated;
revoke execute on function public.colorless_storage_counts() from public, anon, authenticated;
revoke execute on function public.colorless_account_identity_integrity() from public, anon, authenticated;

grant execute on function public.colorless_sync_user(jsonb) to service_role;
grant execute on function public.colorless_sync_room(jsonb) to service_role;
grant execute on function public.colorless_insert_message(jsonb, text, jsonb, integer) to service_role;
grant execute on function public.colorless_insert_message_v2(jsonb, text, jsonb, integer) to service_role;
grant execute on function public.colorless_latest_messages(text[]) to service_role;
grant execute on function public.colorless_unread_counts(jsonb) to service_role;
grant execute on function public.colorless_edit_message(text, text, text, text, text) to service_role;
grant execute on function public.colorless_toggle_message_reaction(text, text, text, text, timestamptz, boolean) to service_role;
grant execute on function public.colorless_delete_message(text, text, text) to service_role;
grant execute on function public.colorless_create_session(text, text, double precision, double precision, integer) to service_role;
grant execute on function public.colorless_session_username(text, double precision) to service_role;
grant execute on function public.colorless_create_account_session(text, text, text, double precision, double precision, integer) to service_role;
grant execute on function public.colorless_account_session_username(text, double precision) to service_role;
grant execute on function public.colorless_switch_session_identity(text, text, text) to service_role;
grant execute on function public.colorless_save_shorts_feed(text, text[], text) to service_role;
grant execute on function public.colorless_acquire_shorts_collection(text, double precision, integer, integer, integer) to service_role;
grant execute on function public.colorless_finish_shorts_collection(text, double precision, integer, boolean, text, integer) to service_role;
grant execute on function public.colorless_publish_event(jsonb, text[], text, double precision, integer) to service_role;
grant execute on function public.colorless_presence_for_user(text) to service_role;
grant execute on function public.colorless_presence_for_users(text[]) to service_role;
grant execute on function public.colorless_touch_presence(text, text, text, text, text, integer) to service_role;
grant execute on function public.colorless_disconnect_presence(text, text) to service_role;
grant execute on function public.colorless_cleanup_presence() to service_role;
grant execute on function public.colorless_storage_counts() to service_role;
grant execute on function public.colorless_account_identity_integrity() to service_role;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'chat-uploads',
  'chat-uploads',
  false,
  8388608,
  array[
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/heic', 'image/heif', 'image/avif',
    'application/pdf', 'audio/webm', 'audio/mp4', 'audio/ogg'
  ]
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
