FROM nginx:1.27-alpine

COPY index.html styles.css app.js install.sh keyboard-killer.svg og.svg og.png robots.txt llms.txt /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
