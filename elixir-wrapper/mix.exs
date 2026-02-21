defmodule ElixirWrapper.MixProject do
  use Mix.Project

  def project do
    [
      app: :elixir_wrapper,
      version: "1.0.0",
      elixir: "~> 1.14",
      start_permanent: Mix.env() == :prod,
      deps: deps()
    ]
  end

  def application do
    [
      extra_applications: [:logger],
      mod: {ElixirWrapper.Application, []}
    ]
  end

  defp deps do
    [
      {:absmartly, path: "../elixir-sdk"},
      {:plug_cowboy, "~> 2.6"},
      {:jason, "~> 1.4"},
      {:uuid, "~> 1.1"}
    ]
  end
end
