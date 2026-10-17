#[derive(Clone, Debug)]
pub struct SessionKeys { pub primary: SecretVec<u8>, pub previous: SecretString }
