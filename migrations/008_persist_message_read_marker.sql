ALTER TABLE users
    ADD COLUMN messages_seen_at DATETIME NULL AFTER login_count;

UPDATE users
SET messages_seen_at = CURRENT_TIMESTAMP
WHERE messages_seen_at IS NULL;
