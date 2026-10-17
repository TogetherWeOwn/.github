fn redact_api_key(s: &str) -> String { if let Some(p) = s.find("apikey=") { return remove_at(s,p); } s.into() }
