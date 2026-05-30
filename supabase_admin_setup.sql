create table if not exists public.milana_admins (
  email text primary key,
  created_at timestamptz not null default now()
);

insert into public.milana_admins (email)
values
  ('milana.admin.72993@gmail.com'),
  ('shmirziyoyev007@gmail.com')
on conflict (email) do nothing;

create or replace function public.is_milana_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.milana_admins
    where lower(email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  );
$$;

grant execute on function public.is_milana_admin() to anon, authenticated;

alter table public.milana_admins enable row level security;

drop policy if exists "Admins can read admin emails" on public.milana_admins;
create policy "Admins can read admin emails"
on public.milana_admins for select
to authenticated
using (public.is_milana_admin());
