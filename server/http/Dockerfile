FROM nginx:stable-alpine

# envsubst ships in the gettext package.
RUN apk add --no-cache gettext

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY boot.ipxe.tmpl /boot.ipxe.tmpl
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
