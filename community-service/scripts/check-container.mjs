import { readConfiguration } from '../src/runtime/environment.mjs';

try {
  const configuration = readConfiguration(process.env.COMMUNITY_CONFIG_FILE, process.env.COMMUNITY_SECRETS_FILE);
  if (!configuration.enabled) throw new Error('disabled');
  const { validateEnvironment } = await import('../dist/runtime/service.mjs');
  await validateEnvironment(configuration.env);
  console.log('community_configuration_valid; schema, tunnel isolation and launch approval require separate verification');
} catch {
  console.error('community_preflight_refused: build the container bundle and provide reviewed enabled configuration and protected secrets');
  process.exitCode = 1;
}