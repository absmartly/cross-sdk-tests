import Config

# Configure Jason as JSON library
config :elixir_wrapper, :json_library, Jason

# Logger configuration
config :logger, :console,
  format: "$time $metadata[$level] $message\n",
  metadata: [:request_id]
