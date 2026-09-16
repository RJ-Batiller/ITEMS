ALTER TABLE chat_messages
    ADD COLUMN reply_to_id BIGINT NULL AFTER sender_id,
    ADD CONSTRAINT fk_chat_messages_reply_to
        FOREIGN KEY (reply_to_id) REFERENCES chat_messages(id) ON DELETE SET NULL;
