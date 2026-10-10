// Jenkins stage (after deploying to staging)
stage('OmniSight visual QA') {
  steps {
    sh '''curl -fsS -X POST "$OMNISIGHT_URL/webhook/ci" -H "X-API-Key: $OMNISIGHT_API_KEY" -H "Content-Type: application/json" \
      -d "{\\"repo\\":\\"org/frontend\\",\\"sha\\":\\"$GIT_COMMIT\\",\\"branch\\":\\"$BRANCH_NAME\\",\\"staging_url\\":\\"$STAGING_URL\\"}"'''
  }
}
