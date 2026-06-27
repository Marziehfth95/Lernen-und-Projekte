from kubernetes import client, config
import time

def monitor_and_heal():
    # Lädt die kubeconfig deines lokalen 'kind' Clusters
    config.load_kube_config()
    
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    
    deployment_name = "finops-agent"
    namespace = "default"
    
    print("🛡️ [Self-Healing Operator] Gestartet. Überwache KI-Pods auf Abstürze...")
    
    while True:
        # Hole alle Pods, die zu unserer KI gehören
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={deployment_name}")
        
        for pod in pods.items:
            # Prüfe den Status der Container im Pod
            if pod.status.container_statuses:
                for status in pod.status.container_statuses:
                    
                    # Suche nach OOMKilled im vorherigen Zustand oder zähle Restarts
                    is_oom = False
                    if status.last_state and status.last_state.terminated:
                        if status.last_state.terminated.reason == "OOMKilled":
                            is_oom = True
                            
                    # Wenn der Pod gecrasht ist (OOMKilled oder allgemeiner CrashLoop)
                    if is_oom or status.restart_count > 0:
                        print(f"\n🚨 ABSTURZ ERKANNT: Pod '{pod.metadata.name}' ist gecrasht!")
                        print("🤖 [Self-Healing Engine] Analysiere Problem: 'Out of Memory' (OOMKilled) vermutet.")
                        print("💉 INITIERE HEILUNG: Skaliere RAM-Limit des Deployments auf 512Mi hoch...")
                        
                        try:
                            # 1. Hole das aktuelle Deployment
                            deployment = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
                            
                            # 2. Patche das RAM Limit von 128Mi auf 512Mi
                            deployment.spec.template.spec.containers[0].resources.limits['memory'] = "512Mi"
                            deployment.spec.template.spec.containers[0].resources.requests['memory'] = "256Mi"
                            
                            # 3. Sende das Update an Kubernetes
                            apps_v1.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=deployment)
                            
                            print("✅ [Self-Healing Engine] HEILUNG ERFOLGREICH! Kubernetes startet den geretteten Pod nun mit mehr Ressourcen neu.")
                            print("🎉 Du kannst die API jetzt erneut aufrufen!")
                            return # Beende den Operator nach erfolgreicher Heilung
                            
                        except Exception as e:
                            print(f"❌ Fehler bei der Heilung: {e}")
                            
        # Warte 3 Sekunden bis zum nächsten Check
        time.sleep(3)

if __name__ == "__main__":
    monitor_and_heal()