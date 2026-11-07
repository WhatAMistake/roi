# Contributing to Roi
Behfore everything: tank you for your interest in contributing to this project! This document provides guidelines for contributing.

## Ways to Contribute

- **Report bugs** - Open an issue with detailed description
- **Suggest features** - Share your ideas for improvements
- **Improve documentation** - Help make docs clearer and more comprehensive
- **Submit code** - Fix bugs or implement new features
- **Translate** - Help improve Russian/English localization
- **Test** - Run tests and report issues

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/WhatAMistake/roi.git`
3. Create a virtual environment: `python -m venv venv`
4. Activate it: `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (Linux/Mac)
5. Install dependencies: `pip install -r requirements.txt`
6. Copy `.env.example` to `.env` and configure your keys
7. Run setup: `python setup.py`

## Code Style

- Follow PEP 8 guidelines
- Use type hints where possible
- Add docstrings for public functions and classes
- Keep functions focused and under 50 lines when possible
- Use meaningful variable names

## Testing

Before submitting changes:

```bash
# Run tests
python -m pytest tests/

# Test the bot locally
python run_telegram.py --model gpt-4o-mini
```

### Update announcements

The bot hashes Python files in `src/` and `app/` and Markdown prompts in
`prompts/`. Only changed-file diffs (up to 12,000 characters) are sent to the
model, in one request for both languages. A missing old snapshot, oversized
diff, or model failure requires admin review rather than silently accepting
incomplete output.

The existing `data/code_hashes.json` also holds snapshots and a pending draft.
Keep this ignored runtime file across deployments and back it up before
maintenance. On first migration, newly tracked files are baselined as they are;
their earlier changes cannot be reconstructed. `python scripts/check_code_cache.py`
is read-only. Do not delete the cache or run cache-reset scripts to diagnose an
issue; the reset scripts refuse to overwrite the new state.

With a valid `ADMIN_ID`, the bot restores the draft after restart. In the private
admin chat, `/updates` checks or resumes it; **править** edits RU and EN,
**да** approves delivery, and **нет** explicitly skips the captured update.
The cancel button only leaves editing; `/updates` restores the draft. Failed
deliveries can be retried with `/updates`; consent is checked again. Delivery is
at least once: a crash after Telegram accepts a message but before the cache is
saved can cause a duplicate. Deploy only one bot process against this cache.
Service logs use `[UPDATE CHECK]` and `[UPDATE BROADCAST]`; do not share the
cache, which contains code snapshots and recipient IDs.

## Commit Messages

Use conventional commit format:

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `style:` - Code style changes (formatting, semicolons, etc)
- `refactor:` - Code refactoring
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

Examples:
```
feat: add voice message support
fix: resolve RAG context retrieval issue
docs: update API setup instructions
```
Pull Request Processcess

1. Create a new branch: `git checkout -b feature/your-feature-name`
2. Make your changes
3. Test thoroughly
4. Commit with clear messages
5. Push to your fork: `git push origin feature/your-feature-name`
6. Open a Pull Request with detailed description
Priority Areasreas

We especially welcome contributions in:

- **Therapeutic techniques** - New existential therapy approaches
- **RAG improvements** - Better context retrieval and relevance
- **Localization** - Improving Russian/English translations
- **Performance** - Optimizing response times
- **Documentation** - Better setup guides and examples
Questions?ons?

- Open an issue for questions
- Check existing issues before creating new ones
- Be respectful and constructive in discussions
Thank You! (once again)You!

Every contribution, no matter how small, helps make this project better for everyone seeking existential therapy support.

