async fn f(req: Request) { let body = req.text().await?; if body.len() > MAX { reject(); } }
