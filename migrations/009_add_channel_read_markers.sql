CREATE TABLE IF NOT EXISTS chat_group_reads (
    group_id INT NOT NULL,
    user_id INT NOT NULL,
    last_read_message_id BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (group_id, user_id),
    FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

INSERT INTO chat_group_reads (group_id, user_id, last_read_message_id)
SELECT gm.group_id, gm.user_id, COALESCE(MAX(m.id), 0)
FROM chat_group_members gm
LEFT JOIN chat_messages m ON m.group_id = gm.group_id
GROUP BY gm.group_id, gm.user_id
ON DUPLICATE KEY UPDATE last_read_message_id = last_read_message_id;
