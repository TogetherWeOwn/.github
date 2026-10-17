fn f(reader: Reader) { reader.take(MAX + 1).read_to_end(&mut body)?; if body.len() > MAX { reject(); } }
