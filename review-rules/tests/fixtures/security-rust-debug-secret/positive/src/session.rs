#[derive(Clone, Debug)]
pub struct SessionKeys { pub primary: Vec<u8>, pub previous: Option<Vec<u8>> }
