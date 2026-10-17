fn f(x: Result<(), E>) { match x { Ok(_) => (), Err(e) => { metrics::increment(); log(e); } } }
