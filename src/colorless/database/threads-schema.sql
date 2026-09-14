-- Additive schema. Social actors always reference users.id, never accounts.id.
create table if not exists thread_meta (id integer primary key check(id=1), revision integer not null);
insert into thread_meta(id,revision) values(1,0) on conflict(id) do nothing;
create table if not exists thread_posts (id text primary key, author_identity_id text not null references users(id), parent_id text references thread_posts(id), root_id text references thread_posts(id), body text not null, visibility text not null check(visibility in ('public','followers')), client_id text not null, created_at text not null, edited_at text not null default '', deleted integer not null default 0 check(deleted in (0,1)), unique(author_identity_id,client_id));
create table if not exists thread_edges (actor_identity_id text not null references users(id), target_identity_id text not null references users(id), kind text not null check(kind in ('follow','block')), created_at text not null, primary key(actor_identity_id,target_identity_id,kind));
create table if not exists thread_likes (post_id text not null references thread_posts(id), actor_identity_id text not null references users(id), created_at text not null, primary key(post_id,actor_identity_id));
create table if not exists thread_notifications (id text primary key, recipient_identity_id text not null references users(id), actor_identity_id text not null references users(id), post_id text references thread_posts(id), kind text not null check(kind in ('follow','like','reply','mention')), created_at text not null, read_at text not null default '');
create table if not exists thread_reports (id text primary key, actor_identity_id text not null references users(id), post_id text not null references thread_posts(id), reason text not null, created_at text not null, unique(actor_identity_id,post_id));
create index if not exists thread_posts_feed_idx on thread_posts(parent_id,id desc);
create index if not exists thread_posts_root_idx on thread_posts(root_id,id);
create index if not exists thread_posts_author_idx on thread_posts(author_identity_id,id desc);
create index if not exists thread_edges_target_idx on thread_edges(target_identity_id,kind,actor_identity_id);
create index if not exists thread_notifications_recipient_idx on thread_notifications(recipient_identity_id,id desc);
create or replace view thread_post_summary as select p.*, (select count(*) from thread_likes l where l.post_id=p.id) as like_count, (select count(*) from thread_posts c where c.parent_id=p.id and c.deleted=0) as reply_count from thread_posts p;

-- All reads and writes are service-role-only. No browser is granted table access.
alter table public.thread_meta enable row level security;
revoke all on public.thread_meta from public,anon,authenticated;
grant all on public.thread_meta to service_role;
alter table public.thread_posts enable row level security;
revoke all on public.thread_posts from public,anon,authenticated;
grant all on public.thread_posts to service_role;
alter table public.thread_edges enable row level security;
revoke all on public.thread_edges from public,anon,authenticated;
grant all on public.thread_edges to service_role;
alter table public.thread_likes enable row level security;
revoke all on public.thread_likes from public,anon,authenticated;
grant all on public.thread_likes to service_role;
alter table public.thread_notifications enable row level security;
revoke all on public.thread_notifications from public,anon,authenticated;
grant all on public.thread_notifications to service_role;
alter table public.thread_reports enable row level security;
revoke all on public.thread_reports from public,anon,authenticated;
grant all on public.thread_reports to service_role;
revoke all on public.thread_post_summary from public,anon,authenticated;
grant select on public.thread_post_summary to service_role;

create or replace function public.colorless_threads_commit(actor_id text, expected_revision integer, mutations jsonb)
returns jsonb language plpgsql security definer set search_path=public as $$
declare actual integer; m jsonb; t text; cols text; keys text; updates text; predicate text;
begin
  select revision into actual from thread_meta where id=1 for update;
  if actual<>expected_revision then return jsonb_build_object('error','conflict'); end if;
  if not colorless_lock_active_identity(actor_id) then return jsonb_build_object('error','forbidden'); end if;
  if jsonb_typeof(mutations)<>'array' or jsonb_array_length(mutations)>100 then raise exception 'invalid mutations'; end if;
  for m in select value from jsonb_array_elements(mutations) loop
    t:=m->>'table';
    case t
      when 'thread_posts' then cols:='id,author_identity_id,parent_id,root_id,body,visibility,client_id,created_at,edited_at,deleted'; keys:='id'; updates:='author_identity_id=excluded.author_identity_id,parent_id=excluded.parent_id,root_id=excluded.root_id,body=excluded.body,visibility=excluded.visibility,client_id=excluded.client_id,created_at=excluded.created_at,edited_at=excluded.edited_at,deleted=excluded.deleted'; predicate:='id=$1->>''id''';
      when 'thread_edges' then cols:='actor_identity_id,target_identity_id,kind,created_at'; keys:='actor_identity_id,target_identity_id,kind'; updates:='created_at=excluded.created_at'; predicate:='actor_identity_id=$1->>''actor_identity_id'' and target_identity_id=$1->>''target_identity_id'' and kind=$1->>''kind''';
      when 'thread_likes' then cols:='post_id,actor_identity_id,created_at'; keys:='post_id,actor_identity_id'; updates:='created_at=excluded.created_at'; predicate:='post_id=$1->>''post_id'' and actor_identity_id=$1->>''actor_identity_id''';
      when 'thread_notifications' then cols:='id,recipient_identity_id,actor_identity_id,post_id,kind,created_at,read_at'; keys:='id'; updates:='recipient_identity_id=excluded.recipient_identity_id,actor_identity_id=excluded.actor_identity_id,post_id=excluded.post_id,kind=excluded.kind,created_at=excluded.created_at,read_at=excluded.read_at'; predicate:='id=$1->>''id''';
      when 'thread_reports' then cols:='id,actor_identity_id,post_id,reason,created_at'; keys:='id'; updates:='actor_identity_id=excluded.actor_identity_id,post_id=excluded.post_id,reason=excluded.reason,created_at=excluded.created_at'; predicate:='id=$1->>''id''';
      else raise exception 'invalid mutation table';
    end case;
    if coalesce((m->>'remove')::boolean,false) then
      execute format('delete from %I where %s',t,predicate) using m->'row';
    else
      execute format('insert into %I(%s) select %s from jsonb_populate_record(null::%I,$1) on conflict(%s) do update set %s',t,cols,cols,t,keys,updates) using m->'row';
    end if;
  end loop;
  update thread_meta set revision=revision+1 where id=1;
  return jsonb_build_object('revision',actual+1);
end;
$$;
revoke all on function public.colorless_threads_commit(text,integer,jsonb) from public,anon,authenticated;
grant execute on function public.colorless_threads_commit(text,integer,jsonb) to service_role;
