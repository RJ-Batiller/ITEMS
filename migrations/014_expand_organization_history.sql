ALTER TABLE organization_history
    MODIFY entity_type ENUM('Category', 'Office', 'Account', 'Equipment') NOT NULL,
    MODIFY action VARCHAR(80) NOT NULL;
