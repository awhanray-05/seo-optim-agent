"""Darwix AI — Automated Website & SEO Optimisation pipeline.

Five agents, run in order by orchestrator.py, exactly as described in
docs/Automated Website & SEO Optimisation.txt and modelled on the
architecture in docs/Optimization Guide (2).html:

  01 SEO Research         -> keyword clusters, intent, content briefs
  02 Page Generation       -> copy, metadata, schema, alt text
  03 Technical SEO QA      -> pass/fail checks before anything is "published"
  04 Publishing & Indexing -> staged page patches + sitemap (no live CMS yet)
  05 SEO Monitoring        -> crawl health, Core Web Vitals proxy, action queue

Shared state lives in data/*.json ("shared data stores" in the architecture
diagram) so any agent can be re-run on its own.
"""
