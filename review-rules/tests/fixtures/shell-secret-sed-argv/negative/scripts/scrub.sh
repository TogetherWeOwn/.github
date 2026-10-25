awk '{gsub(ENVIRON["SECRET"], "[REDACTED]"); print}' input
