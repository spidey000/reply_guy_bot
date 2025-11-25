# Reply Guy Bot

Personal Twitter/X reply automation tool.

## Quick Start

```bash
# Setup
cp .env.example .env
# Edit .env with your credentials

# Install dependencies
pip install -r requirements.txt

# Run
python -m src.bot
```

## Docker

```bash
docker-compose up -d
```

## Configuration

See `.env.example` for all options.

## Development

```bash
pip install -r requirements-dev.txt
pytest tests/
ruff check src/
```
