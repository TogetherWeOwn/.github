fn f() { start().map_err(|e| Error::context(e)); }
