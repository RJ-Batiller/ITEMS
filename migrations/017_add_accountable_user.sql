ALTER TABLE accountability
    ADD COLUMN accountable_user_id INT NULL AFTER equipment_id,
    ADD INDEX idx_accountability_user_current (accountable_user_id, is_current),
    ADD CONSTRAINT fk_accountability_user
        FOREIGN KEY (accountable_user_id) REFERENCES users(id) ON DELETE SET NULL;
