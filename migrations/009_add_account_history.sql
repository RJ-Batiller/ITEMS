ALTER TABLE organization_history
    MODIFY entity_type ENUM('Category', 'Office', 'Account') NOT NULL;
