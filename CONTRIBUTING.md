# Contributing to Existential Therapist Bot

Thank you for your interest in contributing to this project! This document provides guidelines for contributing.

## 🌟 Ways to Contribute

- **Report bugs** - Open an issue with detailed description
- **Suggest features** - Share your ideas for improvements
- **Improve documentation** - Help make docs clearer and more comprehensive
- **Submit code** - Fix bugs or implement new features
- **Translate** - Help improve Russian/English localization
- **Test** - Run tests and report issues

## 🚀 Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/WhatAMistake/roi.git`
3. Create a virtual environment: `python -m venv venv`
4. Activate it: `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (Linux/Mac)
5. Install dependencies: `pip install -r requirements.txt`
6. Copy `.env.example` to `.env` and configure your keys
7. Run setup: `python setup.py`

## 📋 Code Style

- Follow PEP 8 guidelines
- Use type hints where possible
- Add docstrings for public functions and classes
- Keep functions focused and under 50 lines when possible
- Use meaningful variable names

## 🧪 Testing

Before submitting changes:

```bash
# Run tests
python -m pytest tests/

# Test the bot locally
python run_telegram.py --model gpt-4o-mini
```

## 📝 Commit Messages

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

## 🔄 Pull Request Process

1. Create a new branch: `git checkout -b feature/your-feature-name`
2. Make your changes
3. Test thoroughly
4. Commit with clear messages
5. Push to your fork: `git push origin feature/your-feature-name`
6. Open a Pull Request with detailed description

## 🎯 Priority Areas

We especially welcome contributions in:

- **Therapeutic techniques** - New existential therapy approaches
- **RAG improvements** - Better context retrieval and relevance
- **Localization** - Improving Russian/English translations
- **Performance** - Optimizing response times
- **Documentation** - Better setup guides and examples

## 💬 Questions?

- Open an issue for questions
- Check existing issues before creating new ones
- Be respectful and constructive in discussions

## 🙏 Thank You!

Every contribution, no matter how small, helps make this project better for everyone seeking existential therapy support.

