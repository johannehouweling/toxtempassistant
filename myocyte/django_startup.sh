#!/bin/sh
echo "Starting the django_startup.sh file"


# Wait for Postgres *before* doing anything else
if [ "$DATABASE" = "postgres" ]; then
  echo "💻🔗🖥️ Waiting for postgres... "
  while ! nc -z -w 2 "$POSTGRES_HOST" "$POSTGRES_PORT"; do
    sleep 0.2
  done
fi
echo "✅ PostgreSQL is up on $POSTGRES_HOST:$POSTGRES_PORT"

# wait for the database to be ready
echo ">>> Waiting for the database to be ready"
sleep 3

echo ">>> About to migrate"
python3 manage.py migrate --no-input
echo "<<< Migrated."
sleep 0.1

# Stop doing this for a while: it removes the signed-in sessions of existing users.
#echo ">>> Clearing the caches"
#python3.manage.py clear_cache
#echo "<<< Cleared."
#sleep 1

echo ">>> About to collect static files"
# Don't add the "--clear" flag. This removes old apps, and also "documents" directory
# which is needed for file transfer.
python3 manage.py collectstatic --no-input
echo "<<< Collected."
sleep 0.1

# echo ">>> Creating the Django Q_CLUSTER queue. See settings.py file for details."
python3 manage.py createcachetable
sleep 0.1

if [ "${TESTING:-false}" != "true" ]; then
  echo ">>> Scheduling the periodic jobs (see toxtempass/jobs.py)"
  python3 manage.py setup_schedules
  echo ">>> Run clustering tool in background"
  python3 manage.py qcluster &
  sleep 0.1
else
  echo ">>> Skipping qcluster in TESTING mode"
fi

# echo "--- ENVIRONMENT AT STARTUP ---"
# env | sort
# echo "--------------------------------"

# remeber to assign some workers during gunicorn startup
# echo ">>> Run the clustering tool (in the background)."
# python3 manage.py qcluster &
# sleep 0.1

echo "Done setting up Django, exiting the django_startup.sh file."
exec "$@"
