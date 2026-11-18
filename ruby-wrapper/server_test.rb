# HTTP tests of the ruby wrapper's switchback routes, run when the wrapper image is built (see the
# Dockerfile), so a regression fails the ruby lane. Rack::MockRequest drives the Sinatra app
# in-process, with the server's own run turned off.
require 'minitest/autorun'
require 'rack/mock'
require_relative 'server'

set :run, false

class SwitchbackRoutesTest < Minitest::Test
  ROUTES = {
    '/switchback/assign' => { clusterUnit: 'market_1', periodIndex: '0', seed: '0', armCount: 2 },
    '/switchback/permutation' => { clusterUnit: 'market_1', cycleIndex: '0', seed: '0', armCount: 10 }
  }.freeze

  def request
    Rack::MockRequest.new(Sinatra::Application)
  end

  def post(path, body)
    request.post(path, input: body.is_a?(String) ? body : JSON.generate(body), 'CONTENT_TYPE' => 'application/json')
  end

  def test_valid_requests_answer
    assert_equal({ 'result' => 0, 'events' => [] }, JSON.parse(post('/switchback/assign', ROUTES['/switchback/assign']).body))
    assert_equal({ 'result' => [0, 3, 7, 5, 8, 2, 6, 4, 1, 9], 'events' => [] },
                 JSON.parse(post('/switchback/permutation', ROUTES['/switchback/permutation']).body))
  end

  def test_invalid_requests_are_400_with_no_events
    index = { '/switchback/assign' => :periodIndex, '/switchback/permutation' => :cycleIndex }
    ROUTES.each do |path, valid|
      bad = [
        'not json', '[1]',
        valid.merge(index[path] => '9223372036854775808'), valid.merge(index[path] => '-9223372036854775809'),
        valid.merge(index[path] => ' 1'), valid.merge(index[path] => '+1'), valid.merge(index[path] => 1),
        valid.merge(seed: '1_000'), valid.merge(armCount: 1), valid.merge(armCount: MAX_ARM_COUNT + 1),
        valid.merge(armCount: '2'), valid.merge(clusterUnit: 7)
      ]
      bad.each do |body|
        response = post(path, body)
        assert_equal 400, response.status, "#{path} #{body.inspect}"
        parsed = JSON.parse(response.body)
        assert_equal [], parsed['events'], "#{path} #{body.inspect}"
        assert_kind_of String, parsed['error']
      end
    end
  end

  def test_no_assigner_is_501_with_no_events
    original = SwitchbackSelfTest.method(:assigner)
    SwitchbackSelfTest.define_singleton_method(:assigner) { nil }
    ROUTES.each do |path, valid|
      response = post(path, valid)
      assert_equal 501, response.status, path
      assert_equal [], JSON.parse(response.body)['events'], path
    end
  ensure
    SwitchbackSelfTest.define_singleton_method(:assigner, original)
  end
end
