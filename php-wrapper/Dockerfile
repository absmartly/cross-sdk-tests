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

# switchback_self_test_test.php ships with the wrapper directory; running it here fails the image
# build (and so the php lane) when the switchback probe regresses.
RUN php switchback_self_test_test.php /app/vendor/autoload.php

EXPOSE 3000

CMD ["php", "server.php"]
