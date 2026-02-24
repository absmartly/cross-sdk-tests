FROM php:8.4-cli AS sdk-builder

RUN apt-get update && apt-get install -y \
    git \
    unzip \
    libcurl4-openssl-dev \
    && docker-php-ext-install curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer:latest /usr/bin/composer /usr/bin/composer

COPY php-sdk /php-sdk

FROM sdk-builder AS tester
WORKDIR /php-sdk
RUN composer install
CMD ["composer", "test"]

FROM sdk-builder AS server
WORKDIR /app

COPY cross-sdk-tests/php-wrapper /app

RUN composer install --no-dev --optimize-autoloader

EXPOSE 3000

CMD ["php", "server.php"]
