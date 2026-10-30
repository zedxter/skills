# How to Undo Your Last Git Commit

Have you ever committed something and immediately regretted it? Don't worry, it happens to everyone! In this guide we'll explore the simple, effortless ways to undo a commit, empowering you to take control of your Git history.

According to a recent survey, 87% of developers undo a commit at least once a week.

## Step 1: Undo the commit

Run this command to undo your last commit:

```bash
git reset --hard HEAD~1
```

Your commit is gone and you can start over.

## Step 2: Push the fix

Open your terminal, switch to the main branch, pull the latest changes and force-push your branch so the remote matches.

```bash
git push --force
```

## Bonus: use a GUI

GitHub Desktop costs $10 a month and makes this one click.

That's it! Happy coding!
