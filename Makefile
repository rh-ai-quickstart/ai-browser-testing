NAMESPACE      ?= ai-browser-testing
INTERNAL_IMAGE := image-registry.openshift-image-registry.svc:5000/$(NAMESPACE)/ai-browser-testing:latest

.PHONY: deploy undeploy build-cluster deploy-agent logs dashboard status restart

deploy: build-cluster deploy-model deploy-agent dashboard

build-cluster:
	oc new-build --binary --name=ai-browser-testing --strategy=docker -n $(NAMESPACE) 2>/dev/null || true
	oc patch buildconfig ai-browser-testing -n $(NAMESPACE) --type=json \
	  -p '[{"op":"add","path":"/spec/strategy/dockerStrategy/dockerfilePath","value":"Containerfile"}]' 2>/dev/null || true
	oc start-build ai-browser-testing --from-dir=agent/ --follow --wait -n $(NAMESPACE)

deploy-model:
	oc apply -f deploy/01-model-serving.yaml -n $(NAMESPACE)
	@echo "Waiting for model (this may take several minutes)..."
	oc wait --for=condition=Ready inferenceservice/qwen3-8b --timeout=600s -n $(NAMESPACE)

deploy-agent:
	sed 's|quay.io/rh-ai-quickstart/ai-browser-testing:latest|$(INTERNAL_IMAGE)|' \
	  deploy/02-testing-agent.yaml | oc apply -f - -n $(NAMESPACE)
	oc rollout status deployment/browser-testing-agent -n $(NAMESPACE) --timeout=120s

undeploy:
	oc delete project $(NAMESPACE)

logs:
	oc logs -f deployment/browser-testing-agent -n $(NAMESPACE)

dashboard:
	@echo ""
	@echo "========================================"
	@echo "  Dashboard: http://$$(oc get route todo-app -n $(NAMESPACE) -o jsonpath='{.spec.host}')"
	@echo "========================================"
	@echo ""

status:
	@echo "=== Model ==="
	@oc get inferenceservice qwen3-8b -n $(NAMESPACE) -o jsonpath='Ready: {.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null; echo ""
	@echo "=== Agent ==="
	@oc get pods -n $(NAMESPACE) -l app=browser-testing-agent --no-headers 2>/dev/null
	@$(MAKE) --no-print-directory dashboard

restart:
	oc rollout restart deployment/browser-testing-agent -n $(NAMESPACE)
