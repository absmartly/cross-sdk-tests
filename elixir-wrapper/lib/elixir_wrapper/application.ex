defmodule ElixirWrapper.Application do
  @moduledoc false

  use Application

  def start(_type, _args) do
    port = String.to_integer(System.get_env("PORT") || "3000")

    children = [
      {ElixirWrapper.ContextStore, []},
      {Plug.Cowboy, scheme: :http, plug: ElixirWrapper.Router, options: [port: port]}
    ]

    opts = [strategy: :one_for_one, name: ElixirWrapper.Supervisor]
    Supervisor.start_link(children, opts)
  end
end
