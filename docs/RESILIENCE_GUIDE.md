# Resilience & Error Recovery - Operator Guide

## Quick Reference

This guide explains how to monitor and manage the Reply Guy Bot's error recovery features.

## Circuit Breakers

### What are Circuit Breakers?

Circuit breakers protect your bot from cascading failures by automatically stopping calls to failing services.

### States

- **CLOSED** (Green): Normal operation, all calls pass through
- **OPEN** (Red): Service is failing, calls blocked immediately to prevent overload
- **HALF_OPEN** (Yellow): Testing if service has recovered

### How to Check Status

Via health check:
```python
health = await bot.health_check_all()
print(health['twitter']['circuit_breaker'])
```

### What to Do When Circuit Opens

1. **Check the service** - Is the external service (Twitter, AI, Database) down?
2. **Review error logs** - Check recent errors in the logs
3. **Wait for auto-recovery** - Circuit will test recovery after timeout
4. **Manual reset** (if needed):
   ```python
   bot._circuit_breakers['twitter'].reset()
   ```

### Circuit Breaker Configuration

| Service  | Threshold | Recovery Time | Half-Open Calls |
|----------|-----------|---------------|-----------------|
| Twitter  | 5 failures | 120 seconds   | 3 attempts      |
| AI       | 3 failures | 60 seconds    | 2 attempts      |
| Database | 3 failures | 30 seconds    | 2 attempts      |

## Dead Letter Queue (DLQ)

### What is the Dead Letter Queue?

Failed tweet operations are saved for automatic retry instead of being lost.

### Check DLQ Status

```python
stats = await db.get_dead_letter_stats()
print(f"Pending retries: {stats['pending']}")
print(f"Exhausted: {stats['exhausted']}")
```

### DLQ Lifecycle

1. **Tweet fails** → Added to DLQ with retry_count=0
2. **Auto retry** → Retry count incremented
3. **Max retries (5)** → Marked as 'exhausted'
4. **Manual intervention** → Review exhausted items

### Review Failed Tweets

```python
# Get pending items
items = await db.get_dead_letter_items(max_items=10)

for item in items:
    print(f"Tweet: {item['target_tweet_id']}")
    print(f"Error: {item['error']}")
    print(f"Retries: {item['retry_count']}")
```

## Telegram Error Alerts

### Alert Types You'll Receive

| Alert Type | Meaning | Action |
|------------|---------|--------|
| `circuit_breaker_open` | Service circuit opened | Check service health |
| `initialization_failed` | Bot failed to start | Check config and credentials |
| `tweet_processing_failed` | Error processing tweet | Review error details |
| `rate_limit_exceeded` | Hit posting limits | Wait for rate limit reset |
| `multiple_failures` | Consecutive errors | Investigate root cause |

### Alert Format

```
🚨 CRITICAL ALERT

Type: `circuit_breaker_open`
Time: 2025-11-26 14:30:00
Message: AI service circuit breaker opened

Details:
  • service: `ai`
  • error: `Connection timeout`
  • failures: `5`
```

### Responding to Alerts

1. **Don't panic** - System is designed to recover automatically
2. **Check health status** - Use `/stats` command in Telegram
3. **Review logs** - Look for patterns in errors
4. **Take action** - Only intervene if auto-recovery fails

## Health Monitoring

### Quick Health Check

Send `/stats` command to the Telegram bot to see:
- Pending tweets count
- Posted tweets today
- Burst mode status
- Rate limiting status

### Comprehensive Health Check

Run programmatically:
```python
health = await bot.health_check_all()
```

Returns status for:
- Database connection
- Twitter authentication
- AI service availability
- Telegram connection
- Circuit breaker states

### Interpreting Health Status

- **"healthy"**: All systems operational
- **"degraded"**: Some services failing but bot functional
- **"error"**: Critical failure requiring intervention

## Database Connection Recovery

### Auto-Recovery

The bot automatically:
1. Detects connection loss
2. Waits with exponential backoff
3. Retries connection (up to 3 times)
4. Restores normal operation

### What You See

```
WARNING: Database connection lost, attempting reconnect...
INFO: Database connection established
```

### Manual Intervention

If auto-recovery fails repeatedly:
1. Check Supabase dashboard
2. Verify network connectivity
3. Check credentials in .env
4. Restart bot if needed

## Crash Recovery

### Automatic Startup Recovery

On bot startup, it automatically:
1. Loads pending tweets from database
2. Recovers failed tweets for retry
3. Checks dead letter queue
4. Resumes normal operation

### What You See

```
INFO: Performing crash recovery...
INFO: Recovered 3 stale tweets
WARNING: Dead letter queue has 2 pending items, 0 exhausted
INFO: Crash recovery completed
```

### No Action Needed

Recovery is fully automatic. Just monitor the counts to ensure nothing is stuck.

## Retry Behavior

### Exponential Backoff

The bot retries failed operations with increasing delays:

| Attempt | Delay    |
|---------|----------|
| 1       | 1 second |
| 2       | 2 seconds|
| 3       | 4 seconds|
| 4       | 8 seconds|

### Maximum Retries

- **Database operations**: 3 retries
- **Dead letter queue items**: 5 retries
- **Circuit breaker recovery**: Infinite (with timeout)

## Best Practices

### Monitoring

1. **Watch for patterns** - Multiple circuit breaker opens indicate systemic issues
2. **Track DLQ depth** - Growing queue suggests persistent problems
3. **Monitor recovery rate** - Most items should recover automatically
4. **Set up external monitoring** - Use health check endpoint

### Maintenance

1. **Review exhausted DLQ items weekly** - Identify persistent issues
2. **Check circuit breaker logs** - Understand failure patterns
3. **Update thresholds if needed** - Tune for your environment
4. **Archive old audit logs** - `ghost_delegate_audit.log` grows over time

### Troubleshooting

#### Circuit Breaker Won't Close
- Check if underlying service is actually healthy
- Review error logs for persistent issues
- Consider manual reset if service is confirmed working

#### DLQ Items Keep Failing
- Review error messages for root cause
- Check if tweet still exists on Twitter
- Verify Ghost Delegate authentication
- Consider if tweets should be manually rejected

#### Frequent Database Reconnections
- Check Supabase service status
- Verify network stability
- Review connection timeout settings
- Consider increasing circuit breaker threshold

## Emergency Procedures

### Complete System Reset

If all else fails:

1. **Stop the bot**
   ```bash
   docker-compose down
   ```

2. **Clear cookies** (forces re-authentication)
   ```bash
   rm cookies.json
   ```
   > **Note:** Cookies are encrypted with Fernet. After deletion, new cookies will be encrypted automatically on next login if `COOKIE_ENCRYPTION_KEY` is set.

3. **Reset circuit breakers** (via database or restart)

4. **Review configuration**
   ```bash
   cat .env
   ```

5. **Restart with fresh state**
   ```bash
   docker-compose up -d
   ```

### Manual DLQ Processing

For exhausted items that need manual intervention:

```sql
-- Review exhausted items
SELECT * FROM failed_tweets WHERE status = 'exhausted';

-- Manually reset for retry
UPDATE failed_tweets
SET status = 'pending', retry_count = 0
WHERE id = 'item-id-here';

-- Or delete if no longer needed
DELETE FROM failed_tweets WHERE status = 'exhausted';
```

## Metrics to Track

### Key Performance Indicators

1. **Circuit Breaker Opens/Hour** - Should be near zero
2. **DLQ Depth** - Should remain low (<10)
3. **Recovery Success Rate** - Should be >90%
4. **Database Reconnections/Day** - Should be zero
5. **Average Retry Count** - Should be <2

### Alerting Thresholds

Set up alerts for:
- DLQ depth > 20 items
- Circuit breaker open for >5 minutes
- Recovery success rate <80%
- More than 3 database reconnections/hour

## Startup Validation

### Config Validation

On startup, the bot validates all required configuration:
- If any required settings are missing, the bot fails immediately with clear error message
- Cookie encryption key format is validated if provided
- Missing encryption key triggers a security warning (bot continues)

### What You See

```
ERROR: Missing required configuration: DUMMY_USERNAME, TELEGRAM_BOT_TOKEN
```

or

```
WARNING: SECURITY WARNING: COOKIE_ENCRYPTION_KEY not set. Cookies will be stored in plaintext.
```

### Fix

1. Check your `.env` file for missing values
2. Generate encryption key if missing:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. Restart bot

## Support

### Logs to Collect

When reporting issues, include:
1. Application logs (last 500 lines)
2. `ghost_delegate_audit.log`
3. Circuit breaker status
4. DLQ statistics
5. Health check output

### Debug Mode

Enable detailed logging:
```python
logging.basicConfig(level=logging.DEBUG)
```

---

**Remember:** The system is designed to self-heal. Most issues resolve automatically. Only intervene when auto-recovery consistently fails.
