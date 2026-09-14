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
create view if not exists thread_post_summary as select p.*, (select count(*) from thread_likes l where l.post_id=p.id) as like_count, (select count(*) from thread_posts c where c.parent_id=p.id and c.deleted=0) as reply_count from thread_posts p;
