import com.amazonaws.auth.BasicAWSCredentials
import com.amazonaws.services.s3.AmazonS3Client
import java.io._
import org.apache.spark.sql._
import org.apache.spark.sql.types._
import spark.implicits._
import org.apache.hadoop.fs.s3a.Constants._

if (!(sys.env.contains("AWS_ACCESS_KEY_ID") && sys.env.contains("AWS_SECRET_ACCESS_KEY") && sys.env.contains("AWS_SESSION_TOKEN"))) {
  println("Please define both AWS variables to use S3. Continuing in local mode only.")
} else {
  println("Setting up S3 credentials.")
  
  val awsAccessKey = sys.env("AWS_ACCESS_KEY_ID")
  val awsSecretKey = sys.env("AWS_SECRET_ACCESS_KEY")
  val awsSessionToken = sys.env("AWS_SESSION_TOKEN")

  val hadoopConfiguration = spark.sparkContext.hadoopConfiguration
  hadoopConfiguration.set("fs.s3.impl",                      "org.apache.hadoop.fs.s3a.S3AFileSystem")
  hadoopConfiguration.set("fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.TemporaryAWSCredentialsProvider")
  hadoopConfiguration.set("fs.s3a.access.key",               awsAccessKey)
  hadoopConfiguration.set("fs.s3a.secret.key",               awsSecretKey)
  hadoopConfiguration.set("fs.s3a.session.token",            awsSessionToken)
}
