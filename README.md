# Six Seven

Python TUI that queries 7-Eleven gas station prices & locks prices using the 7-Eleven API.

## Usage

To run the program:

```bash
uv run main.py --zip <ZIP_CODE> --bearer-token <BEARER_TOKEN>
```

You will need to provide a valid 7-Eleven bearer token from the 7-Eleven mobile app, which can be scraped using tools like [mitmproxy](https://mitmproxy.org/). Bearer tokens from the website probably won't work, since they don't have the necessary scopes for price locking.

## Disclaimer

This project is for educational purposes only. It is not affiliated with or endorsed by 7-Eleven. Use at your own risk.
