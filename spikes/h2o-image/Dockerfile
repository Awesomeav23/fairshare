# Spike image: H2O-3 server. The jar is copied from the pip h2o package so client and server versions match exactly.
FROM eclipse-temurin:17-jre
RUN useradd --uid 10001 --create-home h2o
COPY --chown=h2o h2o.jar /opt/h2o/h2o.jar
USER 10001
WORKDIR /home/h2o
EXPOSE 54321 54322 8080
# Heap = 60% of the container memory limit.
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=60", "-jar", "/opt/h2o/h2o.jar"]
