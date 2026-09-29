"""hii-digest: email yourself "hii" and get a digest of today's most important emails."""

__version__ = "1.0.0"

# Custom header stamped on every digest we send, so we never answer ourselves.
DIGEST_HEADER = "X-Hii-Digest"
# Header recording which trigger (Gmail message id) a digest answers; used for idempotency.
DIGEST_TRIGGER_HEADER = "X-Hii-Digest-Trigger"
