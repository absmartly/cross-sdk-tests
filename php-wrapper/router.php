<?php

if (php_sapi_name() === 'cli-server') {
    if (preg_match('/\.(?:js|css|png|jpg|jpeg|gif|ico|svg)$/', $_SERVER['REQUEST_URI'])) {
        return false;
    }

    $_SERVER['SCRIPT_NAME'] = '/server.php';
    require 'server.php';
} else {
    require 'server.php';
}
