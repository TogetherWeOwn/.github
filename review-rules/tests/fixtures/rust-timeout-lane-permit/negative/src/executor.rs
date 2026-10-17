async fn f() { tokio::time::timeout(d, harmless()).await; }
