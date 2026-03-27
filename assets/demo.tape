Output assets/demo.gif
Set Width 900
Set Height 550
Set FontSize 14
Set Padding 20
Set Shell "zsh"
Set Theme "Snazzy"
Set TypingSpeed 0
Set BorderRadius 10

Hide
Type "export PATH=/tmp/flash-demo-bin:$PATH && cd ~/Developer/flash-demo"
Enter
Sleep 500ms
Ctrl+L
Sleep 500ms

Set TypingSpeed 40ms
Show
# Flash into — fzf picker
Type "flash into"
Enter
Sleep 1s
Up
Sleep 500ms
Up
Sleep 500ms
Enter
Sleep 2s

# Status right after flash in — should be clean
Type "flash status"
Enter
Sleep 3s

# Visual cue, then commit behind the scenes
Type "# ...make some changes, commit..."
Sleep 2s
Hide
Ctrl+U
Type ". /tmp/demo-prep.sh"
Enter
Sleep 1s
Ctrl+L
Sleep 500ms
Show

# Diff — shows the commit to cherry-pick
Type "flash diff"
Enter
Sleep 3s

# Apply changes back to worktree
Type "flash apply"
Enter
Sleep 2s

# Someone edits the worktree directly
Type "# ...worktree gets edited..."
Sleep 2s
Hide
Ctrl+U
Type ". /tmp/demo-wt-change.sh"
Enter
Sleep 500ms
Ctrl+L
Sleep 500ms
Show

# Diff shows unsynced changes from worktree
Type "flash diff"
Enter
Sleep 3s

# Sync them in
Type "flash sync"
Enter
Sleep 2s

# Flash out
Type "flash out --discard"
Enter
Sleep 2s
