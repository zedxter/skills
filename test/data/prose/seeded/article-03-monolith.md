# Why We Went Back to a Monolith

Some say microservices are always the better architecture, no matter the team or the product. We believed that too, until it nearly sank us.

In 2023 we split our billing system into 14 services. Each team owned two or three of them. Deploys got smaller and more frequent, which is what we wanted.

Then the incidents started. Over the next year we had 31 production incidents caused by a change in one service breaking another. Most of them came from mismatched API versions between services that nobody had tested together.

Studies show that monoliths are three times cheaper to run than microservices.

So in March we merged the 14 services back into one deployable, kept the module boundaries in the code, and ran the whole test suite on every change. Cross-service incidents dropped to two in the following six months. Our next step is Project Falcon, the usage-based pricing engine we launch in Q2, and it will live inside the same monolith.

The lesson is simple: small teams should build monoliths.

In conclusion, the monolith is back, and it is here to stay. Ultimately, architecture is about people, not technology.
