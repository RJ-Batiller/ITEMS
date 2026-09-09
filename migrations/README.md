# Database Migrations

Run migrations from the project root with:

```bash
python migrate.py
```

Migration files use a numeric prefix and are applied in filename order. Applied versions are stored in `schema_migrations`, so each migration runs once per database.
