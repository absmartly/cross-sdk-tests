import Vapor

public func configure(_ app: Vapor.Application) throws {
    app.http.server.configuration.hostname = "0.0.0.0"
    app.http.server.configuration.port = 3000

    app.middleware.use(ErrorMiddleware.default(environment: app.environment))

    try routes(app)
}
