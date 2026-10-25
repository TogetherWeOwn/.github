def select_pr(query, pr_url):
    query.like(pr_url + "%")
