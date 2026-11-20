FROM nginx:1.27-alpine

COPY index.html docs.html styles.css app.js install.sh icon.svg keyless.svg og.svg og.png robots.txt llms.txt /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
