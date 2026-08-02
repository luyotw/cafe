#!/bin/bash
# Setup git hooks from .githooks directory

set -e

echo "Setting up git hooks..."

# Configure git to use .githooks directory
git config --local core.hooksPath .githooks

echo "✅ Git hooks configured successfully!"
echo "   Hooks directory: .githooks"
echo ""
echo "Pre-commit hook will run the selected fast test suite."
echo "Pre-push hook will run the full no-coverage test suite."
echo "Post-commit and post-merge hooks will verify global helper skills."
