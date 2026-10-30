Last spring I spent two days finding out why a nightly job kept skipping a handful of customer records. The code was fine. The bug was a decision someone had made four years earlier: records tagged with a certain legacy flag were deliberately excluded, because a partner system choked on them. Nobody wrote that down. The person who knew had left, and the commit message said "fix edge case."

Two days of two people's time, to recover one sentence.

I think about that sentence a lot. It would have taken about forty seconds to write at the moment the decision was made, when the reason was still obvious and the context was sitting in someone's head. Waiting even a month would have made it harder. Waiting four years made it a small archaeological dig.

Most developers I know don't skip documentation because they're lazy. They skip it because the code feels like it explains itself, and in the moment it does. The trouble is that code tells you what happens. It rarely tells you what was considered and rejected, what the odd constraint was, or which part is temporary and which part is load-bearing. Those things live in memory, and memory is a bad place to keep them.

You also are not writing for a stranger. You are writing for yourself in eight months, when you've forgotten the whole thing and a ticket lands on your desk with the word "urgent" in it. I've read my own old code and wondered who thought this was a good idea. It was me, and I had a reason, and I can't find it.

You don't need a wiki or a style guide. A paragraph at the top of a module explaining why it exists. A comment next to the strange workaround saying what breaks without it. A README that tells a new person how to run the thing without asking anyone. A commit message that says why instead of "fix edge case."

Next time you make a decision that isn't obvious from the code, write one sentence about it before you close the file. Somebody will be glad you did, and there's a decent chance it's you.
