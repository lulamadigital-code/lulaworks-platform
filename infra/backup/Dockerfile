# LulaWorks — backup image.
# postgres:16-alpine gives a version-matched pg_dump; aws-cli lets us copy each
# dump off-box to DigitalOcean Spaces / Amazon S3 (both S3-compatible). The
# backup scripts are bind-mounted by compose, so they aren't COPYed here.
FROM postgres:16-alpine

RUN apk add --no-cache aws-cli
