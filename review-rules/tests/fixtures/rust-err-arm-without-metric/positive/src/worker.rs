fn f(x: Result<(), E>) { match x { Ok(_) => (), Err(e) => { log(e); return; } } }
