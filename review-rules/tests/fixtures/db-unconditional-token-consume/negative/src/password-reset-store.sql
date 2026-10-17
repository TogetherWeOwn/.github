DELETE FROM password_reset_tokens WHERE token_hash = ? AND consumed_at IS NULL;
