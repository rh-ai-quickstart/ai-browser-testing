NAMESPACE ?= ai-browser-testing
RELEASE   ?= ai-browser-testing

.PHONY: install uninstall logs dashboard status restart

install:
	oc new-project $(NAMESPACE) 2>/dev/null || oc project $(NAMESPACE)
	oc label namespace $(NAMESPACE) opendatahub.io/dashboard=true --overwrite
	helm install $(RELEASE) chart/ -n $(NAMESPACE)
	@echo ""
	@echo "Waiting for model (this may take several minutes)..."
	oc wait --for=condition=Ready inferenceservice/qwen3-8b -n $(NAMESPACE) --timeout=600s
	oc rollout status deployment/browser-testing-agent -n $(NAMESPACE) --timeout=120s
	@$(MAKE) --no-print-directory dashboard

uninstall:
	helm uninstall $(RELEASE) -n $(NAMESPACE)
	oc delete project $(NAMESPACE)

upgrade:
	helm upgrade $(RELEASE) chart/ -n $(NAMESPACE)

logs:
	oc logs -f deployment/browser-testing-agent -n $(NAMESPACE)

dashboard:
	@echo ""
	@echo "========================================"
	@echo "  Dashboard: http://$$(oc get route dashboard -n $(NAMESPACE) -o jsonpath='{.spec.host}')"
	@echo "========================================"
	@echo ""

status:
	@echo "=== Model ==="
	@oc get inferenceservice -n $(NAMESPACE) --no-headers 2>/dev/null
	@echo "=== Agent ==="
	@oc get pods -n $(NAMESPACE) -l app=browser-testing-agent --no-headers 2>/dev/null
	@$(MAKE) --no-print-directory dashboard

restart:
	oc rollout restart deployment/browser-testing-agent -n $(NAMESPACE)
