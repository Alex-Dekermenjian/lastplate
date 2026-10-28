# Hosting and persistence decision — LP-6

Recorded 28 October 2026. This is the implementation target for LP-11/LP-15;
no cloud services have been provisioned and this repository is not yet deployed.

| Component | Selected target | Reason |
| --- | --- | --- |
| Django application | Render web service, HTTPS, production WSGI server | Managed deployment of the existing Django project |
| Relational data | Render managed PostgreSQL 17, same region as the application | Persistent relational storage separate from app deployments |
| Uploaded food photos | Cloudinary, using its Python/Django integration | Uploaded images persist outside the app's temporary filesystem |
| CSS/JS/admin assets | collectstatic output served with WhiteNoise during deployment | Assets can be rebuilt from version-controlled source |

Use a database plan with sufficient lifetime and retention for the semester.
Confirm current provider quotas and budget before provisioning in LP-15.
No paid account, subscription or resource is created by this decision document.
If resources cannot support the selected target, record a replacement decision
and verify equivalent persistence before calling the deployment complete.

## Local development already configured

- Shared templates: templates/; app templates remain supported.
- Shared source assets: static/; app static directories remain supported.
- Collected assets: staticfiles/, generated and ignored by Git.
- Local uploads: media/, ignored by Git and served under /media/ only with DEBUG=True.
- .env.example enables local debug explicitly; DEBUG defaults to False.
- Each developer creates their own database and local administrator.

## Implementation and verification during LP-11 / LP-15

1. Add and pin the production server, WhiteNoise and Cloudinary dependencies
   when their integration is implemented; they are not installed by LP-6.
2. Configure production DEBUG=False, exact allowed hosts, HTTPS/CSRF settings,
   fresh environment secrets and the managed database connection.
3. Upload validated listing images from the backend to Cloudinary. Store the
   provider identifier/URL with the listing; keep the API secret server-side.
4. Run migrate and collectstatic during release, using a production server.
   Never run the development media-serving helper on the deployed site.
5. Demonstrate that a shop, listing and photo survive app restart/redeploy.
6. Configure database backup/export and document restore instructions. Test
   recovery before final release. Account deletion/cleanup is a separate action.

The local media directory and Render's default filesystem are not the selected
production photo store. A successful local admin login is not deployment evidence.

## Primary references

- [Django on Render](https://render.com/docs/deploy-django)
- [Managed Render Postgres](https://render.com/docs/postgresql-creating-connecting)
- [Render filesystem persistence](https://render.com/docs/disks)
- [Cloudinary Python/Django integration](https://cloudinary.com/documentation/django_integration)
- [Django static files](https://docs.djangoproject.com/en/5.2/howto/static-files/)
