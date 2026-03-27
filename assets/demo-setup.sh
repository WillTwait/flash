#!/usr/bin/env bash
# Setup a throwaway repo for VHS demo recording.
# Run once before `vhs assets/demo.tape`.
set -euo pipefail

DEMO=~/Developer/flash-demo
rm -rf "$DEMO" /tmp/flash-demo-bin
rm -rf ~/Developer/flash-demo-backend-refactor
rm -rf ~/Developer/flash-demo-bugfix
rm -rf ~/Developer/flash-demo-improve-status
rm -rf ~/Developer/flash-demo-syntax-highlighting
rm -rf ~/Developer/flash-demo-auto-complete

# Create a wrapper outside the repo so branch switches can't remove it
mkdir -p /tmp/flash-demo-bin
cat > /tmp/flash-demo-bin/flash << 'WRAPPER'
#!/usr/bin/env bash
exec uv run --project ~/Developer/flash flash "$@"
WRAPPER
chmod +x /tmp/flash-demo-bin/flash

# Create a repo with a couple of files
mkdir -p "$DEMO" && cd "$DEMO"
git init -b main
echo "# my-app" > README.md
echo 'print("hello")' > app.py
git add -A && git commit -m "Initial commit"

# Create worktrees (feature is the one we'll flash into)
git branch backend-refactor
git branch bugfix
git branch improve-status
git branch syntax-highlighting
git branch auto-complete

git worktree add ../flash-demo-backend-refactor backend-refactor
git worktree add ../flash-demo-bugfix bugfix
git worktree add ../flash-demo-improve-status improve-status
git worktree add ../flash-demo-syntax-highlighting syntax-highlighting
git worktree add ../flash-demo-auto-complete auto-complete

# Add some changes to the bugfix worktree
cd ../flash-demo-bugfix
echo 'print("new feature")' > feature.py
echo '# updated' >> app.py
git add -A && git commit -m "Add new feature"
# No uncommitted changes — keeps the demo clean on flash into

cd "$DEMO"

# Scripts for hidden changes during demo
cat > /tmp/demo-prep.sh << 'SCRIPT'
echo bugfix > fix.py && git add fix.py && git commit -m 'Fix bug'
SCRIPT

cat > /tmp/demo-wt-change.sh << SCRIPT
echo '# hotfix' >> $HOME/Developer/flash-demo-bugfix/app.py
SCRIPT

echo "Demo repo ready at $DEMO"
