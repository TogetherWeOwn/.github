const hits = text.split("
").filter(line => /console\.error\(/.test(line));
