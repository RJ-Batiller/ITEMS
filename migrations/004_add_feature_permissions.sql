ALTER TABLE users
    ADD COLUMN feature_permissions JSON NULL
    AFTER role_id;
